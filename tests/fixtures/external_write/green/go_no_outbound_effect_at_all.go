// GREEN. Nothing here reaches outside the process.
package worker

func Caption(target *Target) string {
	if !target.HasPermission {
		return ""
	}
	return target.CaptionFinal + " #ad"
}
