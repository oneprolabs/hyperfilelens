package remote

import (
	"context"
	cryptorand "crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math/big"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"

	"hyperfilelens/agent/internal/infra/config"
	"hyperfilelens/agent/internal/platform/tlsclient"
	"hyperfilelens/agent/internal/wire"
)

// ErrNotConnected is returned when the uplink has no active socket.
var ErrNotConnected = errors.New("websocket not connected")

const handshakeTimeout = 30 * time.Second

// wsIdleReadTimeout is how long the read loop may block without local or peer activity.
// Must exceed HeartbeatInterval (30s) so the first periodic heartbeat can refresh
// activity before the read deadline fires (tickers fire after the first interval).
const wsKeepaliveInterval = 10 * time.Second
const wsReadTimeout = 90 * time.Second
const wsWriteTimeout = 10 * time.Second

func wsIdleReadTimeout() time.Duration {
	return wsReadTimeout
}

// Connector maintains an outbound agent ↔ WS Node WebSocket session.
type Connector struct {
	mu       sync.RWMutex
	writeMu  sync.Mutex
	conn     *websocket.Conn
	provider config.Provider

	activityMu   sync.Mutex
	lastActivity time.Time

	dialer            websocket.Dialer
	backoff           func(attempt int) time.Duration
	heartbeatInterval time.Duration
	keepaliveInterval time.Duration

	onHeartbeat func(ctx context.Context) map[string]any
}

func (c *Connector) touchActivity() {
	c.activityMu.Lock()
	c.lastActivity = time.Now()
	c.activityMu.Unlock()
}

func (c *Connector) readDeadline() time.Time {
	c.activityMu.Lock()
	last := c.lastActivity
	c.activityMu.Unlock()
	if last.IsZero() {
		return time.Now().Add(wsIdleReadTimeout())
	}
	return last.Add(wsIdleReadTimeout())
}

// SetHeartbeatHook registers a callback that supplies optional metrics for periodic heartbeats.
func (c *Connector) SetHeartbeatHook(hook func(ctx context.Context) map[string]any) {
	c.mu.Lock()
	c.onHeartbeat = hook
	c.mu.Unlock()
}

func (c *Connector) heartbeatPayload(ctx context.Context) map[string]any {
	c.mu.RLock()
	hook := c.onHeartbeat
	c.mu.RUnlock()
	if hook == nil {
		return nil
	}
	return hook(ctx)
}

// NewConnector configures dial metadata from a hot-reloadable config Provider.
func NewConnector(provider config.Provider) *Connector {
	return &Connector{
		provider: provider,
		dialer: websocket.Dialer{
			HandshakeTimeout: handshakeTimeout,
			Proxy:            http.ProxyFromEnvironment,
			TLSClientConfig:  tlsclient.Config(),
			Subprotocols:     []string{wire.TaskResultAckSubprotocol},
		},
		backoff:           BackoffDelay,
		heartbeatInterval: wire.HeartbeatInterval,
		keepaliveInterval: wsKeepaliveInterval,
	}
}

// TaskResultAckEnabled reports whether the current peer negotiated durable result ACKs.
func (c *Connector) TaskResultAckEnabled() bool {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.conn != nil && c.conn.Subprotocol() == wire.TaskResultAckSubprotocol
}

// LocalIPAddress returns the local address of the active control-plane connection.
func (c *Connector) LocalIPAddress() string {
	c.mu.RLock()
	conn := c.conn
	c.mu.RUnlock()
	if conn == nil {
		return ""
	}
	local := conn.UnderlyingConn().LocalAddr()
	if tcpAddress, ok := local.(*net.TCPAddr); ok && tcpAddress.IP != nil {
		return tcpAddress.IP.String()
	}
	host, _, err := net.SplitHostPort(local.String())
	if err != nil {
		return ""
	}
	return strings.TrimSpace(host)
}

func (c *Connector) endpoint() (baseURL, agentID, token string) {
	if c.provider == nil {
		return "", "", ""
	}
	cfg := c.provider.Current()
	return strings.TrimSpace(cfg.WSSURL), strings.TrimSpace(cfg.NodeID), strings.TrimSpace(cfg.NodeToken)
}

func (c *Connector) buildDialURL() (string, error) {
	baseURL, agentID, token := c.endpoint()
	u, err := url.Parse(baseURL)
	if err != nil {
		return "", err
	}
	q := u.Query()
	if q.Get("node_id") == "" && agentID != "" {
		q.Set("node_id", agentID)
	}
	if q.Get("token") == "" && token != "" {
		q.Set("token", token)
	}
	u.RawQuery = q.Encode()
	return u.String(), nil
}

func (c *Connector) setConn(conn *websocket.Conn) {
	c.mu.Lock()
	c.conn = conn
	c.mu.Unlock()
}

// SendJSON sends a single text frame uplink (heartbeat, task.progress, task.result, …).
func (c *Connector) SendJSON(_ context.Context, v any) error {
	payload, err := json.Marshal(v)
	if err != nil {
		return err
	}
	c.mu.RLock()
	conn := c.conn
	c.mu.RUnlock()
	if conn == nil {
		return ErrNotConnected
	}
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	_ = conn.SetWriteDeadline(time.Now().Add(wsWriteTimeout))
	err = conn.WriteMessage(websocket.TextMessage, payload)
	if err == nil {
		c.touchActivity()
	}
	return err
}

// Run reconnects with exponential backoff until ctx is cancelled.
// onConnected is invoked after each successful dial (used to flush pending task results).
func (c *Connector) Run(
	ctx context.Context,
	onMessage func(context.Context, []byte) error,
	onConnected func(context.Context) error,
) error {
	attempt := 1
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		baseURL, _, _ := c.endpoint()
		if baseURL == "" {
			return fmt.Errorf("missing websocket url")
		}

		err, connected := c.connectOnce(ctx, onMessage, onConnected, attempt > 1)
		if ctxErr := ctx.Err(); ctxErr != nil &&
			(errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded)) {
			return ctxErr
		}
		if err != nil && !errors.Is(err, ioClosed) {
			slog.Warn("websocket session ended", "err", err, "attempt", attempt)
		}

		delay := c.retryDelay(attempt)
		if connected {
			attempt = 1
		} else {
			attempt++
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(delay):
		}
	}
}

// Sentinel to avoid logging clean close as warning noise.
var ioClosed = errors.New("websocket closed")

func (c *Connector) connectOnce(
	ctx context.Context,
	onMessage func(context.Context, []byte) error,
	onConnected func(context.Context) error,
	reconnected bool,
) (error, bool) {
	dialURL, err := c.buildDialURL()
	if err != nil {
		return err, false
	}
	_, agentID, token := c.endpoint()
	if agentID == "" || token == "" {
		slog.Warn(
			"websocket credentials incomplete",
			"node_id_set", agentID != "",
			"token_set", token != "",
		)
	}
	if hint := wssPathHint(dialURL); hint != "" {
		slog.Warn("websocket url may be incorrect", "hint", hint, "url", redactDialURL(dialURL))
	}

	dialCtx, cancel := context.WithTimeout(ctx, c.dialer.HandshakeTimeout)
	defer cancel()

	conn, resp, err := c.dialer.DialContext(dialCtx, dialURL, nil)
	if err != nil {
		if resp != nil {
			body, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
			_ = resp.Body.Close()
			slog.Warn(
				"websocket dial failed",
				"url", redactDialURL(dialURL),
				"status", resp.StatusCode,
				"response", strings.TrimSpace(string(body)),
				"err", err,
			)
		} else {
			slog.Warn("websocket dial failed", "url", redactDialURL(dialURL), "err", err)
		}
		return err, false
	}
	_ = conn.SetWriteDeadline(time.Time{})
	c.touchActivity()
	conn.SetPongHandler(func(string) error {
		c.touchActivity()
		return nil
	})
	c.setConn(conn)
	sessionCtx, sessionCancel := context.WithCancel(ctx)
	defer sessionCancel()
	nodeID := ""
	if c.provider != nil {
		nodeID = c.provider.Current().NodeID
	}
	slog.Info("websocket connected", "node_id", nodeID, "url", redactDialURL(dialURL))
	if reconnected {
		slog.Info("websocket reconnected", "node_id", nodeID)
	}

	done := make(chan struct{})
	sessionErr := make(chan error, 1)

	go c.heartbeatLoop(sessionCtx, done, sessionErr)
	go c.keepaliveLoop(sessionCtx, done, sessionErr)

	go func() {
		for {
			_ = conn.SetReadDeadline(c.readDeadline())
			_, msg, rerr := conn.ReadMessage()
			if rerr != nil {
				select {
				case sessionErr <- rerr:
				default:
				}
				return
			}
			c.touchActivity()
			if onMessage != nil {
				if err := onMessage(sessionCtx, msg); err != nil {
					select {
					case sessionErr <- err:
					default:
					}
					return
				}
			}
		}
	}()

	// Start the read loop before the connection hook. The hook can flush a
	// bounded result window, and peers are allowed to ACK those frames
	// immediately while the hook continues its reconnect work.
	if onConnected != nil {
		if hookErr := onConnected(sessionCtx); hookErr != nil {
			slog.Warn("websocket onConnected hook failed", "err", hookErr)
		}
	}

	var loopErr error
	select {
	case <-ctx.Done():
		loopErr = ctx.Err()
	case err := <-sessionErr:
		loopErr = err
	}

	close(done)
	sessionCancel()
	c.setConn(nil)
	_ = conn.Close()

	if loopErr != nil && websocket.IsCloseError(loopErr, websocket.CloseNormalClosure, websocket.CloseGoingAway) {
		return ioClosed, true
	}
	return loopErr, true
}

func (c *Connector) heartbeatLoop(ctx context.Context, done <-chan struct{}, sessionErr chan<- error) {
	t := time.NewTicker(c.currentHeartbeatInterval())
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-done:
			return
		case <-t.C:
			payload := c.heartbeatPayload(ctx)
			var sendErr error
			if len(payload) > 0 {
				sendErr = wire.SendHeartbeatWithPayload(context.Background(), c, payload)
			} else {
				sendErr = wire.SendHeartbeat(context.Background(), c)
			}
			if sendErr != nil {
				slog.Warn("websocket heartbeat send failed", "err", sendErr)
				select {
				case sessionErr <- sendErr:
				default:
				}
				return
			}
			c.touchActivity()
		}
	}
}

func (c *Connector) keepaliveLoop(ctx context.Context, done <-chan struct{}, sessionErr chan<- error) {
	t := time.NewTicker(c.currentKeepaliveInterval())
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-done:
			return
		case <-t.C:
			if err := c.SendPing(); err != nil {
				slog.Warn("websocket ping failed", "err", err)
				select {
				case sessionErr <- err:
				default:
				}
				return
			}
		}
	}
}

func (c *Connector) SendPing() error {
	c.mu.RLock()
	conn := c.conn
	c.mu.RUnlock()
	if conn == nil {
		return ErrNotConnected
	}
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	err := conn.WriteControl(
		websocket.PingMessage,
		nil,
		time.Now().Add(wsWriteTimeout),
	)
	if err == nil {
		c.touchActivity()
	}
	return err
}

func (c *Connector) retryDelay(attempt int) time.Duration {
	if c.backoff != nil {
		return withJitter(c.backoff(attempt), 0.2)
	}
	return BackoffDelay(attempt)
}

func (c *Connector) currentHeartbeatInterval() time.Duration {
	if c.heartbeatInterval > 0 {
		return c.heartbeatInterval
	}
	return wire.HeartbeatInterval
}

func (c *Connector) currentKeepaliveInterval() time.Duration {
	if c.keepaliveInterval > 0 {
		return c.keepaliveInterval
	}
	return wsKeepaliveInterval
}

// BackoffDelay returns the base reconnect delay for attempt n (1-based).
func BackoffDelay(attempt int) time.Duration {
	if attempt < 1 {
		attempt = 1
	}
	delay := time.Second << min(attempt-1, 4)
	return min(delay, 15*time.Second)
}

func withJitter(delay time.Duration, ratio float64) time.Duration {
	if delay <= 0 || ratio <= 0 {
		return delay
	}
	maxJitter := time.Duration(float64(delay) * ratio)
	if maxJitter <= 0 {
		return delay
	}
	n, err := cryptorand.Int(cryptorand.Reader, big.NewInt(int64(maxJitter*2)+1))
	if err != nil {
		return delay
	}
	return delay + time.Duration(n.Int64()) - maxJitter
}

func redactDialURL(raw string) string {
	u, err := url.Parse(raw)
	if err != nil {
		return raw
	}
	q := u.Query()
	if q.Get("token") != "" {
		q.Set("token", "<redacted>")
	}
	u.RawQuery = q.Encode()
	return u.String()
}

func wssPathHint(raw string) string {
	u, err := url.Parse(raw)
	if err != nil {
		return ""
	}
	path := strings.TrimSpace(u.Path)
	if path == "/ws/agent/" || path == "/ws/agent" {
		return "use /ws/node/agent/ (not /ws/agent/)"
	}
	if path == "/ws/node/agent" {
		return "WSS path should end with /ws/node/agent/"
	}
	if path != "" && !strings.HasPrefix(path, "/ws/node/agent") {
		return "expected path /ws/node/agent/"
	}
	return ""
}
