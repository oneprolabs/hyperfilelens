package engine

import (
	"strings"
	"testing"
)

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
