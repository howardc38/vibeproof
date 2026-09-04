package analysis

import (
	"fmt"
	"example.com/app/app"
)

func Rule() int { fmt.Print(1); return app.Main() }
