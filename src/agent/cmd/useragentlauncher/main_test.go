package main

import "testing"

func TestParseDataDir(t *testing.T) {
	path := `C:\Users\operator\AppData\Local\HyperFileLens\AgentData`
	got, ok := parseDataDir([]string{"-data-dir", path})
	if !ok || got != path {
		t.Fatalf("parseDataDir() = %q, %v; want %q, true", got, ok, path)
	}
}

func TestParseDataDirRejectsInvalidArguments(t *testing.T) {
	for _, args := range [][]string{
		nil,
		{"-data-dir"},
		{"-data-dir", ""},
		{"--data-dir", `C:\data`},
		{"-data-dir", `C:\data`, "extra"},
	} {
		if _, ok := parseDataDir(args); ok {
			t.Fatalf("parseDataDir(%q) unexpectedly succeeded", args)
		}
	}
}
