// Package sentinel exports solve functions for external use.
package sentinel

// SolveTurnstileDXWithSession is the exported wrapper around solveTurnstileDXWithSession.
func SolveTurnstileDXWithSession(requirementsToken, dx string, session *Session) (string, error) {
	return solveTurnstileDXWithSession(requirementsToken, dx, session)
}

// SolveTurnstileDX is the exported wrapper around solveTurnstileDX.
func SolveTurnstileDX(requirementsToken, dx string) (string, error) {
	return solveTurnstileDX(requirementsToken, dx)
}
