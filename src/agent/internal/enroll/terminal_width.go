package enroll

import (
	"io"
	"os"
	"strconv"
	"strings"
)

func terminalColumns(writer io.Writer) int {
	if value := strings.TrimSpace(os.Getenv("COLUMNS")); value != "" {
		if columns, err := strconv.Atoi(value); err == nil && columns >= 40 {
			return columns
		}
	}
	file, ok := writer.(*os.File)
	if !ok {
		return 0
	}
	return nativeTerminalColumns(file)
}
