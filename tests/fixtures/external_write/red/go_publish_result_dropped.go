// RED -- readback. The publish happens and its answer is thrown away.
//
// `Publish` returns the created media's id and an error. Dropping both leaves
// the row with no marker, so nothing downstream can tell this post from one
// that was never published, and the next attempt publishes again. The function
// calls nothing from the outbound-read table either, so there is no second
// source of the answer.
package worker

type Worker struct {
	meta *MetaAdapter
	repo *PostRepo
}

func (w *Worker) Run(target *Target, staged []Item) {
	w.meta.Publish(target.PostID, staged, target.Caption)
	w.repo.MarkDone(target.PostID)
}
