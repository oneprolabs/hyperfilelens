package enroll

import (
	"bytes"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sync"
	"time"
)

type commandLogSink struct {
	mu     sync.Mutex
	buffer bytes.Buffer
	file   *os.File
	path   string
}

type commandLogStream struct {
	sink        *commandLogSink
	line        []byte
	escapeState uint8
}

func (stream *commandLogStream) Write(content []byte) (int, error) {
	for _, value := range content {
		if stream.escapeState == 1 {
			if value == '[' {
				stream.escapeState = 2
			} else {
				stream.escapeState = 0
			}
			continue
		}
		if stream.escapeState == 2 {
			if value >= 0x40 && value <= 0x7e {
				stream.escapeState = 0
			}
			continue
		}
		if value == 0x1b {
			stream.escapeState = 1
			continue
		}
		switch value {
		case '\r':
			stream.line = stream.line[:0]
		case '\n':
			stream.flush()
		default:
			stream.line = append(stream.line, value)
		}
	}
	return len(content), nil
}

func (stream *commandLogStream) flush() {
	timestamp := time.Now().UTC().Format("2006-01-02T15:04:05.000Z")
	line := []byte("[" + timestamp + "] ")
	line = append(line, stream.line...)
	line = append(line, '\n')
	_, _ = stream.sink.Write(line)
	stream.line = stream.line[:0]
}

func (stream *commandLogStream) close() {
	if len(stream.line) > 0 {
		stream.flush()
	}
}

func (sink *commandLogSink) Write(content []byte) (int, error) {
	sink.mu.Lock()
	defer sink.mu.Unlock()
	if sink.file != nil {
		_, _ = sink.file.Write(content)
	} else {
		_, _ = sink.buffer.Write(content)
	}
	// Installer logging is diagnostic and must never fail the installation.
	return len(content), nil
}

func (sink *commandLogSink) commit(path string) error {
	sink.mu.Lock()
	defer sink.mu.Unlock()
	if sink.file != nil {
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	if sink.buffer.Len() > 0 {
		if _, err := file.Write(sink.buffer.Bytes()); err != nil {
			_ = file.Close()
			return err
		}
	}
	sink.buffer.Reset()
	sink.file = file
	sink.path = path
	return nil
}

func (sink *commandLogSink) close() {
	sink.mu.Lock()
	defer sink.mu.Unlock()
	if sink.file != nil {
		_ = sink.file.Sync()
		_ = sink.file.Close()
		sink.file = nil
	}
}

type commandLogCapture struct {
	sink           *commandLogSink
	originalStdout *os.File
	originalStderr *os.File
	stdoutRead     *os.File
	stdoutWrite    *os.File
	stderrRead     *os.File
	stderrWrite    *os.File
	stdoutLog      *commandLogStream
	stderrLog      *commandLogStream
	wait           sync.WaitGroup
	closeOnce      sync.Once
}

var commandLogState struct {
	sync.Mutex
	current *commandLogCapture
}

var resolveInstallLogPath = installLogPath

// StartCommandLogging mirrors output in memory until installation begins.
func StartCommandLogging() func() {
	stdoutRead, stdoutWrite, err := os.Pipe()
	if err != nil {
		return func() {}
	}
	stderrRead, stderrWrite, err := os.Pipe()
	if err != nil {
		_ = stdoutRead.Close()
		_ = stdoutWrite.Close()
		return func() {}
	}
	capture := &commandLogCapture{
		sink:           &commandLogSink{},
		originalStdout: os.Stdout,
		originalStderr: os.Stderr,
		stdoutRead:     stdoutRead,
		stdoutWrite:    stdoutWrite,
		stderrRead:     stderrRead,
		stderrWrite:    stderrWrite,
	}
	capture.stdoutLog = &commandLogStream{sink: capture.sink}
	capture.stderrLog = &commandLogStream{sink: capture.sink}
	os.Stdout = stdoutWrite
	os.Stderr = stderrWrite
	capture.wait.Add(2)
	go capture.copyOutput(stdoutRead, capture.originalStdout, capture.stdoutLog)
	go capture.copyOutput(stderrRead, capture.originalStderr, capture.stderrLog)
	commandLogState.Lock()
	commandLogState.current = capture
	commandLogState.Unlock()
	return capture.close
}

func (capture *commandLogCapture) copyOutput(
	source *os.File,
	terminal *os.File,
	logStream *commandLogStream,
) {
	defer capture.wait.Done()
	defer logStream.close()
	_, _ = io.Copy(io.MultiWriter(terminal, logStream), source)
}

func (capture *commandLogCapture) close() {
	capture.closeOnce.Do(func() {
		commandLogState.Lock()
		if commandLogState.current == capture {
			commandLogState.current = nil
		}
		commandLogState.Unlock()
		os.Stdout = capture.originalStdout
		os.Stderr = capture.originalStderr
		_ = capture.stdoutWrite.Close()
		_ = capture.stderrWrite.Close()
		capture.wait.Wait()
		_ = capture.stdoutRead.Close()
		_ = capture.stderrRead.Close()
		capture.sink.close()
	})
}

func commitInstallLog() {
	commandLogState.Lock()
	capture := commandLogState.current
	commandLogState.Unlock()
	if capture == nil {
		return
	}
	path := resolveInstallLogPath()
	if err := capture.sink.commit(path); err != nil {
		logWarn("Install log could not be persisted; terminal logging will continue: " + err.Error())
		return
	}
	logOK(fmt.Sprintf("Install log enabled (%s).", path))
}

func activeInstallLogPath() string {
	commandLogState.Lock()
	capture := commandLogState.current
	commandLogState.Unlock()
	if capture == nil {
		return ""
	}
	capture.sink.mu.Lock()
	defer capture.sink.mu.Unlock()
	return capture.sink.path
}

func commandStdout() *os.File {
	commandLogState.Lock()
	capture := commandLogState.current
	commandLogState.Unlock()
	if capture != nil && capture.originalStdout != nil {
		return capture.originalStdout
	}
	return os.Stdout
}
