// BYPASS. The read that silences the scope is a local one.
//
// `os.ReadFile` reports its own failure in-process and cannot answer "did the
// publish land". One local read must not silence a network write, which is a
// defect this module carried on the Python side until it was measured.
package worker

import "os"

func (w *Worker) Run(target *Target, staged []Item) {
	body, _ := os.ReadFile(target.CaptionPath)
	w.meta.Publish(target.PostID, staged, string(body))
}
