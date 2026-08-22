package main

import "os"

func main() {
	dataDir, ok := parseDataDir(os.Args[1:])
	if !ok {
		os.Exit(2)
	}
	os.Exit(runAgent(dataDir))
}

func parseDataDir(args []string) (string, bool) {
	if len(args) != 2 || args[0] != "-data-dir" || args[1] == "" {
		return "", false
	}
	return args[1], true
}
