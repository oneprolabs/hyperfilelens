package selfupdate

import "strings"

// Version is the agent semver or pre-release tag (overridden by -ldflags at link time).
var Version = "0.1.0"

// Commit is the VCS revision embedded at build time.
var Commit = "unknown"

// BuildIdentity identifies one exact Agent build.
type BuildIdentity struct {
	Version string
	Commit  string
}

// CurrentBuildIdentity returns the coherent identity embedded in this binary.
func CurrentBuildIdentity() BuildIdentity {
	return BuildIdentity{
		Version: strings.TrimSpace(Version),
		Commit:  strings.TrimSpace(Commit),
	}
}

// NeedsUpdate reports whether remoteVersion is newer than the running build.
func NeedsUpdate(remoteVersion string) bool {
	return remoteVersion != "" && remoteVersion != Version
}
