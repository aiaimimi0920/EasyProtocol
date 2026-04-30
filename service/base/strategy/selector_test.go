package strategy

import (
	"testing"

	"easy_protocol/config"
)

func TestSequentialSelectorRotates(t *testing.T) {
	selector := New(config.SelectorSequential)
	candidates := []Candidate{
		{Service: "JSProtocol"},
		{Service: "GolangProtocol"},
	}

	first, err := selector.Select(candidates)
	if err != nil {
		t.Fatalf("first select failed: %v", err)
	}
	second, err := selector.Select(candidates)
	if err != nil {
		t.Fatalf("second select failed: %v", err)
	}

	if first.Service != "GolangProtocol" {
		t.Fatalf("expected first selection to be GolangProtocol, got %s", first.Service)
	}
	if second.Service != "JSProtocol" {
		t.Fatalf("expected second selection to be JSProtocol, got %s", second.Service)
	}
}

func TestBalanceSelectorChoosesLeastActive(t *testing.T) {
	selector := New(config.SelectorBalance)
	candidates := []Candidate{
		{Service: "RustProtocol", ActiveRequests: 3},
		{Service: "PythonProtocol", ActiveRequests: 1},
		{Service: "GolangProtocol", ActiveRequests: 1},
	}

	selected, err := selector.Select(candidates)
	if err != nil {
		t.Fatalf("select failed: %v", err)
	}

	if selected.Service != "GolangProtocol" {
		t.Fatalf("expected lexical tie-break on least-active candidates, got %s", selected.Service)
	}
}
