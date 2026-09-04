package app

import (
	"example.com/app/pkg"
	"example.com/app/pkg/analysis"
)

func Main() int { return pkg.Q() + analysis.Rule() }
