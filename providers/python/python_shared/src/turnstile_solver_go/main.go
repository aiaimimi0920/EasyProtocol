package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	"turnstile_solver/sentinel"
)

type request struct {
	DX      string            `json:"dx"`
	P       string            `json:"p"`
	Session *sentinel.Session `json:"session,omitempty"`
}

type response struct {
	Token string `json:"token"`
	Error string `json:"error,omitempty"`
}

func main() {
	var input []byte
	var err error
	if len(os.Args) >= 2 {
		input = []byte(os.Args[1])
	} else {
		input, err = io.ReadAll(os.Stdin)
		if err != nil {
			out, _ := json.Marshal(response{Error: fmt.Sprintf("read stdin: %v", err)})
			fmt.Println(string(out))
			os.Exit(1)
		}
	}
	var req request
	if err := json.Unmarshal(input, &req); err != nil {
		out, _ := json.Marshal(response{Error: fmt.Sprintf("invalid json: %v", err)})
		fmt.Println(string(out))
		os.Exit(1)
	}

	token, err := sentinel.SolveTurnstileDXWithSession(req.P, req.DX, req.Session)
	if err != nil {
		out, _ := json.Marshal(response{Error: err.Error()})
		fmt.Println(string(out))
		os.Exit(1)
	}

	out, _ := json.Marshal(response{Token: token})
	fmt.Println(string(out))
}
