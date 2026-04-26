package webhook

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"

	"orison/channels/core"
)

type Channel struct {
	Addr string
}

func New(addr string) Channel {
	if addr == "" {
		addr = "127.0.0.1:8787"
	}
	return Channel{Addr: addr}
}

func (c Channel) Name() string { return "http" }

func (c Channel) Run(ctx context.Context, handler core.Handler) error {
	mux := http.NewServeMux()
	server := &http.Server{
		Addr:              c.Addr,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
	}
	webhookToken := strings.TrimSpace(os.Getenv("ORISON_WEBHOOK_TOKEN"))
	maxBodyBytes := int64(1 << 20)

	mux.HandleFunc("/message", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method not allowed"})
			return
		}
		if webhookToken != "" && r.Header.Get("Authorization") != "Bearer "+webhookToken {
			writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)
		var payload struct {
			SessionID string         `json:"session_id"`
			UserID    string         `json:"user_id"`
			Text      string         `json:"text"`
			Message   string         `json:"message"`
			Metadata  map[string]any `json:"metadata"`
		}
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "request body must be JSON"})
			return
		}
		text := payload.Text
		if text == "" {
			text = payload.Message
		}
		if strings.TrimSpace(payload.UserID) == "" || strings.TrimSpace(text) == "" {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "user_id and text are required"})
			return
		}
		sessionID := payload.SessionID
		if sessionID == "" {
			sessionID = "http:" + payload.UserID
		}

		var responseText string
		msg := core.Message{
			SessionID: sessionID,
			Channel:   "http",
			UserID:    payload.UserID,
			Text:      text,
			Metadata:  payload.Metadata,
			Reply: func(ctx context.Context, reply string) error {
				responseText = reply
				return nil
			},
		}
		if err := handler.HandleMessage(r.Context(), msg); err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"session_id": sessionID,
			"response":   responseText,
		})
	})

	errCh := make(chan error, 1)
	go func() {
		fmt.Printf("Orison HTTP webhook channel listening on http://%s/message\n", c.Addr)
		errCh <- server.ListenAndServe()
	}()

	select {
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdownCtx); err != nil {
			return err
		}
		return ctx.Err()
	case err := <-errCh:
		if err == http.ErrServerClosed {
			return nil
		}
		return err
	}
}

func writeJSON(w http.ResponseWriter, status int, payload map[string]any) {
	body, _ := json.Marshal(payload)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}
