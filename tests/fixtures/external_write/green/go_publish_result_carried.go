// GREEN. The answer is kept and the failure is handed on.
package worker

func (w *Worker) Run(target *Target, staged []Item) error {
	id, err := w.meta.Publish(target.PostID, staged, target.Caption)
	if err != nil {
		return err
	}
	return w.repo.MarkPublished(target.PostID, id)
}
