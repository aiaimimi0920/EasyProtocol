package services

import "reflect"

func DiffRuntimeStates(before, after RuntimeState) map[string]any {
	diff := map[string]any{}

	if !reflect.DeepEqual(before.Policy.Global, after.Policy.Global) {
		diff["global"] = map[string]any{
			"before": before.Policy.Global,
			"after":  after.Policy.Global,
		}
	}

	operationDiffs := map[string]any{}
	seenOperations := make(map[string]struct{})
	for operation := range before.Policy.Operations {
		seenOperations[operation] = struct{}{}
	}
	for operation := range after.Policy.Operations {
		seenOperations[operation] = struct{}{}
	}
	for operation := range seenOperations {
		left, leftOK := before.Policy.Operations[operation]
		right, rightOK := after.Policy.Operations[operation]
		switch {
		case !leftOK && rightOK:
			operationDiffs[operation] = map[string]any{"after": right}
		case leftOK && !rightOK:
			operationDiffs[operation] = map[string]any{"before": left}
		case !reflect.DeepEqual(left, right):
			operationDiffs[operation] = map[string]any{
				"before": left,
				"after":  right,
			}
		}
	}
	if len(operationDiffs) > 0 {
		diff["operations"] = operationDiffs
	}

	serviceDiffs := map[string]any{}
	seenServices := make(map[string]struct{})
	for name := range before.Services {
		seenServices[name] = struct{}{}
	}
	for name := range after.Services {
		seenServices[name] = struct{}{}
	}
	for name := range seenServices {
		left, leftOK := before.Services[name]
		right, rightOK := after.Services[name]
		switch {
		case !leftOK && rightOK:
			serviceDiffs[name] = map[string]any{"after": right}
		case leftOK && !rightOK:
			serviceDiffs[name] = map[string]any{"before": left}
		case !reflect.DeepEqual(left, right):
			serviceDiffs[name] = map[string]any{
				"before": left,
				"after":  right,
			}
		}
	}
	if len(serviceDiffs) > 0 {
		diff["services"] = serviceDiffs
	}

	return diff
}
