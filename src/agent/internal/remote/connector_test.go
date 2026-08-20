package remote

import (
	"context"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gorilla/websocket"

	"hyperfilelens/agent/internal/model"
	"hyperfilelens/agent/internal/wire"
)

type staticProvider struct {
	cfg *model.AgentConfig
}

func (p staticProvider) Current() *model.AgentConfig {
	return p.cfg
}

func TestConnectorRunRetriesDialTimeout(t *testing.T) {
	provider := staticProvider{cfg: &model.AgentConfig{
		WSSURL:    "ws://example.test/ws/node/agent/",
		NodeID:    "1",
		NodeToken: "token",
	}}
	connector := NewConnector(provider)
	connector.backoff = func(int) time.Duration { return time.Millisecond }
	connector.dialer.HandshakeTimeout = 5 * time.Millisecond

	var attempts atomic.Int32
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	connector.dialer.NetDialContext = func(ctx context.Context, network, addr string) (net.Conn, error) {
		if attempts.Add(1) >= 2 {
			cancel()
		}
		<-ctx.Done()
		return nil, ctx.Err()
	}

	err := connector.Run(ctx, nil, nil)
	if err != context.Canceled {
		t.Fatalf("Run() error = %v, want context.Canceled", err)
	}
	if got := attempts.Load(); got < 2 {
		t.Fatalf("dial attempts = %d, want at least 2", got)
	}
}

func TestHeartbeatLoopReportsWriteFailure(t *testing.T) {
	upgrader := websocket.Upgrader{}
	closed := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			t.Errorf("upgrade: %v", err)
			return
		}
		_ = conn.Close()
		close(closed)
	}))
	defer server.Close()

	wsURL := "ws" + strings.TrimPrefix(server.URL, "http")
	clientConn, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		t.Fatalf("dial websocket: %v", err)
	}
	defer clientConn.Close()

	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("server did not close websocket")
	}

	connector := NewConnector(staticProvider{cfg: &model.AgentConfig{}})
	connector.heartbeatInterval = time.Millisecond
	connector.setConn(clientConn)
	if got := connector.LocalIPAddress(); net.ParseIP(got) == nil {
		t.Fatalf("LocalIPAddress()=%q, want active socket address", got)
	}

	done := make(chan struct{})
	sessionErr := make(chan error, 1)
	defer close(done)
	go connector.heartbeatLoop(context.Background(), done, sessionErr)

	select {
	case err := <-sessionErr:
		if err == nil {
			t.Fatal("heartbeatLoop reported nil error")
		}
	case <-time.After(time.Second):
		t.Fatal("heartbeatLoop did not report write failure")
	}
}

func TestReadDeadlineExtendsAfterActivity(t *testing.T) {
	connector := NewConnector(staticProvider{cfg: &model.AgentConfig{}})
	connector.touchActivity()
	first := connector.readDeadline()
	time.Sleep(20 * time.Millisecond)
	connector.touchActivity()
	second := connector.readDeadline()
	if !second.After(first) {
		t.Fatalf("read deadline did not extend: first=%v second=%v", first, second)
	}
}

func TestBackoffDelayCappedAtFifteenSeconds(t *testing.T) {
	if got := BackoffDelay(10); got != 15*time.Second {
		t.Fatalf("BackoffDelay(10) = %v, want 15s", got)
	}
}

func TestConnectorIdleSessionSurvivesHeartbeats(t *testing.T) {
	upgrader := websocket.Upgrader{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			t.Errorf("upgrade: %v", err)
			return
		}
		defer conn.Close()
		for {
			if _, _, err := conn.ReadMessage(); err != nil {
				return
			}
		}
	}))
	defer server.Close()

	wsURL := "ws" + strings.TrimPrefix(server.URL, "http")
	connector := NewConnector(staticProvider{cfg: &model.AgentConfig{
		WSSURL:    wsURL,
		NodeID:    "1",
		NodeToken: "token",
	}})
	connector.heartbeatInterval = 40 * time.Millisecond
	connector.dialer.HandshakeTimeout = 2 * time.Second

	ctx, cancel := context.WithTimeout(context.Background(), 400*time.Millisecond)
	defer cancel()

	err, _ := connector.connectOnce(ctx, func(context.Context, []byte) error { return nil }, nil, false)
	if err != nil && err != context.DeadlineExceeded {
		t.Fatalf("connectOnce ended early: %v", err)
	}
}

func TestConnectorNegotiatesTaskResultAckSubprotocol(t *testing.T) {
	upgrader := websocket.Upgrader{Subprotocols: []string{wire.TaskResultAckSubprotocol}}
	negotiated := make(chan bool, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			t.Errorf("upgrade: %v", err)
			return
		}
		defer conn.Close()
		negotiated <- conn.Subprotocol() == wire.TaskResultAckSubprotocol
		_ = conn.WriteControl(
			websocket.CloseMessage,
			websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""),
			time.Now().Add(time.Second),
		)
	}))
	defer server.Close()

	connector := NewConnector(staticProvider{cfg: &model.AgentConfig{
		WSSURL:    "ws" + strings.TrimPrefix(server.URL, "http"),
		NodeID:    "1",
		NodeToken: "token",
	}})
	connector.heartbeatInterval = time.Hour
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	_, _ = connector.connectOnce(ctx, nil, func(context.Context) error {
		if !connector.TaskResultAckEnabled() {
			t.Error("connector did not expose negotiated ACK mode")
		}
		return nil
	}, false)
	if !<-negotiated {
		t.Fatal("server did not negotiate task result ACK subprotocol")
	}
}

func TestConnectorReadsFramesWhileOnConnectedHookRuns(t *testing.T) {
	upgrader := websocket.Upgrader{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			t.Errorf("upgrade: %v", err)
			return
		}
		defer conn.Close()
		if err := conn.WriteMessage(websocket.TextMessage, []byte(`{"type":"task.result.ack","task_id":"task-1"}`)); err != nil {
			t.Errorf("write frame: %v", err)
			return
		}
		_, _, _ = conn.ReadMessage()
	}))
	defer server.Close()

	connector := NewConnector(staticProvider{cfg: &model.AgentConfig{
		WSSURL:    "ws" + strings.TrimPrefix(server.URL, "http"),
		NodeID:    "1",
		NodeToken: "token",
	}})
	connector.heartbeatInterval = time.Hour
	messageRead := make(chan struct{})
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	err, _ := connector.connectOnce(
		ctx,
		func(context.Context, []byte) error {
			close(messageRead)
			return nil
		},
		func(context.Context) error {
			select {
			case <-messageRead:
				cancel()
				return nil
			case <-time.After(250 * time.Millisecond):
				return errors.New("read loop did not run during onConnected hook")
			}
		},
		false,
	)
	if err != context.Canceled {
		t.Fatalf("connectOnce() error = %v, want context.Canceled", err)
	}
}

func TestKeepaliveLoopSendsWebSocketPing(t *testing.T) {
	pinged := make(chan struct{}, 1)
	upgrader := websocket.Upgrader{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			t.Errorf("upgrade: %v", err)
			return
		}
		defer conn.Close()
		conn.SetPingHandler(func(appData string) error {
			select {
			case pinged <- struct{}{}:
			default:
			}
			return conn.WriteControl(
				websocket.PongMessage,
				[]byte(appData),
				time.Now().Add(time.Second),
			)
		})
		for {
			if _, _, err := conn.ReadMessage(); err != nil {
				return
			}
		}
	}))
	defer server.Close()

	wsURL := "ws" + strings.TrimPrefix(server.URL, "http")
	clientConn, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		t.Fatalf("dial websocket: %v", err)
	}
	defer clientConn.Close()

	connector := NewConnector(staticProvider{cfg: &model.AgentConfig{}})
	connector.keepaliveInterval = time.Millisecond
	connector.setConn(clientConn)

	done := make(chan struct{})
	sessionErr := make(chan error, 1)
	defer close(done)
	go connector.keepaliveLoop(context.Background(), done, sessionErr)

	select {
	case <-pinged:
	case err := <-sessionErr:
		t.Fatalf("keepaliveLoop error = %v", err)
	case <-time.After(time.Second):
		t.Fatal("keepaliveLoop did not send websocket ping")
	}
}

func TestRunResetsBackoffAfterSuccessfulConnection(t *testing.T) {
	upgrader := websocket.Upgrader{}
	var accepted atomic.Int32
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			t.Errorf("upgrade: %v", err)
			return
		}
		defer conn.Close()
		accepted.Add(1)
		_ = conn.WriteControl(
			websocket.CloseMessage,
			websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""),
			time.Now().Add(time.Second),
		)
	}))
	defer server.Close()

	wsURL := "ws" + strings.TrimPrefix(server.URL, "http")
	connector := NewConnector(staticProvider{cfg: &model.AgentConfig{
		WSSURL:    wsURL,
		NodeID:    "1",
		NodeToken: "token",
	}})
	connector.heartbeatInterval = time.Hour

	var mu sync.Mutex
	var attempts []int
	connector.backoff = func(attempt int) time.Duration {
		mu.Lock()
		attempts = append(attempts, attempt)
		if len(attempts) >= 2 {
			cancel()
		}
		mu.Unlock()
		return time.Millisecond
	}

	err := connector.Run(ctx, nil, nil)
	if err != context.Canceled {
		t.Fatalf("Run() error = %v, want context.Canceled", err)
	}
	mu.Lock()
	got := append([]int(nil), attempts...)
	mu.Unlock()
	if !reflect.DeepEqual(got, []int{1, 1}) {
		t.Fatalf("backoff attempts = %v, want [1 1]", got)
	}
}
