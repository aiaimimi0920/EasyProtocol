package strategy

import (
	"errors"
	"math/rand"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"easy_protocol/config"
)

var ErrNoCandidates = errors.New("no candidates available")

type Candidate struct {
	Service        string
	ActiveRequests int64
}

type Selector interface {
	Mode() config.SelectorMode
	Select(candidates []Candidate) (Candidate, error)
}

func New(mode config.SelectorMode) Selector {
	switch mode {
	case config.SelectorRandom:
		return &RandomSelector{rng: rand.New(rand.NewSource(time.Now().UnixNano()))}
	case config.SelectorBalance:
		return &BalanceSelector{}
	default:
		return &SequentialSelector{}
	}
}

func sortedCandidates(candidates []Candidate) []Candidate {
	out := append([]Candidate(nil), candidates...)
	sort.Slice(out, func(i, j int) bool {
		if out[i].ActiveRequests == out[j].ActiveRequests {
			return out[i].Service < out[j].Service
		}
		return out[i].ActiveRequests < out[j].ActiveRequests
	})
	return out
}

func Ordered(mode config.SelectorMode, candidates []Candidate) []Candidate {
	switch mode {
	case config.SelectorRandom:
		return sortedCandidates(candidates)
	case config.SelectorBalance:
		return sortedCandidates(candidates)
	default:
		return sortedCandidates(candidates)
	}
}

type SequentialSelector struct {
	counter atomic.Uint64
}

func (s *SequentialSelector) Mode() config.SelectorMode { return config.SelectorSequential }

func (s *SequentialSelector) Select(candidates []Candidate) (Candidate, error) {
	if len(candidates) == 0 {
		return Candidate{}, ErrNoCandidates
	}
	ordered := sortedCandidates(candidates)
	index := (s.counter.Add(1) - 1) % uint64(len(ordered))
	return ordered[index], nil
}

type RandomSelector struct {
	mu  sync.Mutex
	rng *rand.Rand
}

func (s *RandomSelector) Mode() config.SelectorMode { return config.SelectorRandom }

func (s *RandomSelector) Select(candidates []Candidate) (Candidate, error) {
	if len(candidates) == 0 {
		return Candidate{}, ErrNoCandidates
	}
	ordered := sortedCandidates(candidates)
	s.mu.Lock()
	defer s.mu.Unlock()
	return ordered[s.rng.Intn(len(ordered))], nil
}

type BalanceSelector struct {
	counter atomic.Uint64
}

func (s *BalanceSelector) Mode() config.SelectorMode { return config.SelectorBalance }

func (s *BalanceSelector) Select(candidates []Candidate) (Candidate, error) {
	if len(candidates) == 0 {
		return Candidate{}, ErrNoCandidates
	}
	ordered := sortedCandidates(candidates)
	minActive := ordered[0].ActiveRequests
	leastLoadedCount := 1
	for leastLoadedCount < len(ordered) && ordered[leastLoadedCount].ActiveRequests == minActive {
		leastLoadedCount++
	}
	index := (s.counter.Add(1) - 1) % uint64(leastLoadedCount)
	return ordered[index], nil
}
