package nas

import "fmt"

const (
	MountHelperMissing  = "NAS_MOUNT_HELPER_MISSING"
	MountHelperUnusable = "NAS_MOUNT_HELPER_UNUSABLE"
)

// MountHelperError reports a missing or unusable system NAS mount helper.
// Its message intentionally avoids assigning a source or proxy role because
// the same Agent operation can execute on either kind of node.
type MountHelperError struct {
	Code       string
	Operation  string
	Dependency string
	Helper     string
	Cause      string
}

func (e *MountHelperError) Error() string {
	if e.Code == MountHelperUnusable {
		return fmt.Sprintf(
			"%s: %s is installed but not usable (%s failed to start: %s)",
			e.Operation,
			e.Dependency,
			e.Helper,
			e.Cause,
		)
	}
	return fmt.Sprintf(
		"%s: %s is not installed (missing %s helper)",
		e.Operation,
		e.Dependency,
		e.Helper,
	)
}

// SMBCharsetUnavailableError means the executing host kernel cannot provide the
// configured CIFS filename charset. Continuing without it can corrupt paths.
type SMBCharsetUnavailableError struct {
	Charset string
	Kernel  string
	Cause   string
}

func (e *SMBCharsetUnavailableError) Error() string {
	kernel := e.Kernel
	if kernel == "" {
		kernel = "the running kernel"
	}
	return fmt.Sprintf(
		"Host cannot mount the SMB share because filename charset %q requires the nls_utf8 kernel module, which is unavailable for %s.",
		e.Charset, kernel,
	)
}
