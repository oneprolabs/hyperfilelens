package enroll

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func TestResolveGatewayLensConfigUsesEnrolledControlPlaneOrigin(t *testing.T) {
	lens, err := resolveGatewayLensConfig(
		"https://console.example:11443",
		LensSidecarConfig{
			LensBaseURL:  "https://127.0.0.1:11443/sourcelens",
			LensBasePath: "/sourcelens",
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	if lens.LensBaseURL != "https://console.example:11443/sourcelens" {
		t.Fatalf("LensBaseURL = %q", lens.LensBaseURL)
	}
}

func TestResolveGatewayLensConfigKeepsInstallerManagedLoopback(t *testing.T) {
	lens, err := resolveGatewayLensConfig(
		"https://127.0.0.1:11443",
		LensSidecarConfig{
			LensBaseURL:  "https://console.example:11443/sourcelens",
			LensBasePath: "/sourcelens",
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	if lens.LensBaseURL != "https://127.0.0.1:11443/sourcelens" {
		t.Fatalf("LensBaseURL = %q", lens.LensBaseURL)
	}
}

func TestResolveGatewayLensConfigKeepsExternalSourceLensURL(t *testing.T) {
	lens, err := resolveGatewayLensConfig(
		"https://console.example:11443",
		LensSidecarConfig{LensBaseURL: "https://lens.example/custom"},
	)
	if err != nil {
		t.Fatal(err)
	}
	if lens.LensBaseURL != "https://lens.example/custom" {
		t.Fatalf("LensBaseURL = %q", lens.LensBaseURL)
	}
}

func TestResolveGatewayLensConfigRejectsNetworkPath(t *testing.T) {
	_, err := resolveGatewayLensConfig(
		"https://console.example:11443",
		LensSidecarConfig{
			LensBaseURL:  "https://lens.example",
			LensBasePath: "//attacker.example/sourcelens",
		},
	)
	if err == nil {
		t.Fatal("resolveGatewayLensConfig accepted a network-path reference")
	}
}

func TestVerifyLensEndpoint(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.String() != "https://console.example:11443/sourcelens/health" {
			t.Fatalf("health URL = %q", request.URL.String())
		}
		return &http.Response{
			StatusCode: http.StatusOK,
			Status:     "200 OK",
			Body:       io.NopCloser(strings.NewReader("ok")),
		}, nil
	})}

	if err := verifyLensEndpointWithClient(
		context.Background(),
		client,
		"https://console.example:11443/sourcelens",
	); err != nil {
		t.Fatal(err)
	}
}

func TestVerifyLensEndpointRejectsUnhealthyResponse(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(_ *http.Request) (*http.Response, error) {
		return &http.Response{
			StatusCode: http.StatusServiceUnavailable,
			Status:     "503 Service Unavailable",
			Body:       io.NopCloser(strings.NewReader("unavailable")),
		}, nil
	})}

	if err := verifyLensEndpointWithClient(
		context.Background(),
		client,
		"https://lens.example",
	); err == nil {
		t.Fatal("verifyLensEndpoint accepted HTTP 503")
	}
}
