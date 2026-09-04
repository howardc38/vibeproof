// RED -- readback. Both results assigned to the one name Go has for
// discarding.
//
// `fail-closed` does not see this: its Go half reads `_ = err` where `err` is
// a plain name, and here the right-hand side is the call itself.
package worker

func Notify(bot *Bot, chatID int64, text string) {
	_, _ = bot.SendMessage(chatID, text)
}
