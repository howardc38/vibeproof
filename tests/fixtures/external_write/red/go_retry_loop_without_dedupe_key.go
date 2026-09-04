// RED -- replay. The file re-runs the write itself and nothing dedupes it.
//
// The loop names an attempt and sleeps between them, which is what tells a
// retry from a fan-out: `for _, post := range posts` sends N different
// messages on purpose.
package worker

import "time"

func Generate(model *Model, prompt string) error {
	for attempt := 0; attempt < 3; attempt++ {
		if err := model.GenerateContent(prompt); err == nil {
			return nil
		}
		time.Sleep(time.Second)
	}
	return ErrExhausted
}
