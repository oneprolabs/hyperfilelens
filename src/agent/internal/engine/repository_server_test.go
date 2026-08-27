package engine

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/pem"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"hyperfilelens/agent/internal/model"
)

func TestPrepareRepositoryServerCertificateUsesSessionScopedLongLivedTLSMaterial(t *testing.T) {
	dir := t.TempDir()
	certPath := filepath.Join(dir, "server.crt")
	keyPath := filepath.Join(dir, "server.key")
	fingerprint, err := prepareRepositoryServerCertificate(certPath, keyPath, "proxy.example.test")
	if err != nil {
		t.Fatal(err)
	}
	if len(fingerprint) != 64 {
		t.Fatalf("fingerprint length=%d, want SHA-256 hex", len(fingerprint))
	}
	storedFingerprint, err := certificateFingerprint(certPath)
	if err != nil {
		t.Fatal(err)
	}
	if storedFingerprint != fingerprint {
		t.Fatalf("stored fingerprint=%q, generated fingerprint=%q", storedFingerprint, fingerprint)
	}
	certData, err := os.ReadFile(certPath)
	if err != nil {
		t.Fatal(err)
	}
	block, _ := pem.Decode(certData)
	if block == nil {
		t.Fatal("certificate is not PEM encoded")
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		t.Fatal(err)
	}
	if cert.NotAfter.Before(time.Now().Add(364 * 24 * time.Hour)) {
		t.Fatalf("certificate expires too soon: %s", cert.NotAfter)
	}
	now := time.Now()
	if now.Before(cert.NotBefore) || now.After(cert.NotAfter) {
		t.Fatal("generated certificate is not currently valid")
	}
	if info, err := os.Stat(keyPath); err != nil {
		t.Fatal(err)
	} else if info.Mode().Perm() != 0o600 {
		t.Fatalf("private key mode=%o, want 600", info.Mode().Perm())
	}
	if _, err := tls.LoadX509KeyPair(certPath, keyPath); err != nil {
		t.Fatalf("generated certificate and private key do not match: %v", err)
	}
}

func TestRepositoryServerErrorCodeExtractsOnlyStableDiagnosticPrefix(t *testing.T) {
	if got := repositoryServerErrorCode("REPOSITORY_SERVER_READY_TIMEOUT: details"); got != "REPOSITORY_SERVER_READY_TIMEOUT" {
		t.Fatalf("code=%q", got)
	}
	if got := repositoryServerErrorCode("kopia server exited"); got != "" {
		t.Fatalf("unexpected code=%q", got)
	}
}

func TestRepositoryServerStopFailsClosedForUnreadableSessionState(t *testing.T) {
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: t.TempDir()}})
	sessionID := "unreadable-session"
	statePath := engine.repositoryServerSessionStatePath(sessionID)
	if err := os.MkdirAll(filepath.Dir(statePath), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(statePath, []byte("{"), 0o600); err != nil {
		t.Fatal(err)
	}

	status, result, message := engine.runRepositoryServerStop(
		context.Background(),
		Payload{Extra: map[string]any{"session_id": sessionID}},
	)
	if status != "failed" {
		t.Fatalf("status=%q, want failed", status)
	}
	if got := result["error_code"]; got != "REPOSITORY_SERVER_STOP_FAILED" {
		t.Fatalf("error_code=%v", got)
	}
	if !strings.Contains(message, "Session state is unreadable") {
		t.Fatalf("message=%q", message)
	}
	if _, err := os.Stat(statePath); err != nil {
		t.Fatalf("unreadable state must be retained for safe diagnosis: %v", err)
	}
}

func TestRepositoryServerReconcileRejectsMismatchedSessionIdentity(t *testing.T) {
	engine := New(staticConfigProvider{cfg: &model.AgentConfig{DataDir: t.TempDir()}})
	sessionID := "session-a"
	sessionDir := engine.repositoryServerSessionDir(sessionID)
	if err := os.MkdirAll(sessionDir, 0o700); err != nil {
		t.Fatal(err)
	}
	statePath := engine.repositoryServerSessionStatePath(sessionID)
	if err := writeRepositoryServerSession(statePath, repositoryServerSession{SessionID: "session-b"}); err != nil {
		t.Fatal(err)
	}

	err := engine.reconcilePersistedRepositoryServerSession(sessionID, sessionDir)
	if err == nil || !strings.Contains(err.Error(), "identity does not match") {
		t.Fatalf("error=%v", err)
	}
	if _, err := os.Stat(statePath); err != nil {
		t.Fatalf("mismatched state must be retained: %v", err)
	}
}

func TestFreeRepositoryServerPortUsesManagedRange(t *testing.T) {
	engine := &Engine{}
	port, err := engine.reserveRepositoryServerPortWithProbe(
		"127.0.0.1",
		func(string, int) bool { return true },
	)
	if err != nil {
		t.Fatal(err)
	}
	defer engine.releaseRepositoryServerPort(port)
	if port < repositoryServerPortMin || port > repositoryServerPortMax {
		t.Fatalf(
			"port=%d, want managed range %d-%d",
			port,
			repositoryServerPortMin,
			repositoryServerPortMax,
		)
	}
}

func TestRepositoryServerPortReservationsDoNotOverlap(t *testing.T) {
	engine := &Engine{}
	available := func(string, int) bool { return true }
	first, err := engine.reserveRepositoryServerPortWithProbe("127.0.0.1", available)
	if err != nil {
		t.Fatal(err)
	}
	defer engine.releaseRepositoryServerPort(first)

	second, err := engine.reserveRepositoryServerPortWithProbe("127.0.0.1", available)
	if err != nil {
		t.Fatal(err)
	}
	defer engine.releaseRepositoryServerPort(second)
	if first == second {
		t.Fatalf("concurrent reservations reused port %d", first)
	}
}

func TestRepositoryServerPortReservationReportsManagedRangeExhaustion(t *testing.T) {
	engine := &Engine{repositoryServerPorts: make(map[int]struct{})}
	for port := repositoryServerPortMin; port <= repositoryServerPortMax; port++ {
		engine.repositoryServerPorts[port] = struct{}{}
	}

	_, err := engine.reserveRepositoryServerPortWithProbe(
		"127.0.0.1",
		func(string, int) bool { return true },
	)
	if err == nil {
		t.Fatal("expected managed port range exhaustion")
	}
	want := "TCP range 51515-52014"
	if !strings.Contains(err.Error(), want) {
		t.Fatalf("error=%q, want substring %q", err, want)
	}
}
