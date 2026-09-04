package analysis

// This used to import "example.com/app/app" and now does it the long way.
import "example.com/app/app"

func Rule() int { return app.Main() }
