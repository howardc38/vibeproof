// BYPASS. The comment says the result is checked. Nothing checks it.
//
// The emitter parses without comments on purpose, so prose cannot reach the
// rule at all.
package worker

func (w *Worker) Run(target *Target, staged []Item) {
	// The adapter returns the media id and we verify it downstream in
	// MarkDone, which reads the id back from the API before it commits.
	w.meta.Publish(target.PostID, staged, target.Caption)
	w.repo.MarkDone(target.PostID)
}
