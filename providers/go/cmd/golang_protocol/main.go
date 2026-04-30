package main

import (
	"errors"
	"log"
	"net/http"
	"os"

	"golang_protocol/internal/server"
)

func main() {
	addr := "127.0.0.1:11001"
	if value := os.Getenv("GOLANG_PROTOCOL_LISTEN"); value != "" {
		addr = value
	}
	srv := server.New(addr)
	log.Printf("GolangProtocol listening on %s", addr)
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatalf("golang protocol server failed: %v", err)
	}
}
