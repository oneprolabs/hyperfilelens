package config

// Field describes one HFL_* setting for load, dump, and persistence.
type Field struct {
	Env         string
	Key         string
	Secret      bool
	Persistent  bool
	ReadOnly    bool
	Description string
}

// Registry lists all agent runtime settings in one place.
var Registry = []Field{
	{Env: "HFL_WSS_URL", Key: "wss_url", Persistent: true, Description: "Control plane WebSocket URL"},
	{Env: "HFL_API_BASE", Key: "api_base_url", Persistent: true, Description: "HTTPS API base URL"},
	{Env: "HFL_CONTROL_PLANE_API", Key: "api_base_url", Persistent: false, Description: "Alias for HFL_API_BASE"},
	{Env: "HFL_ORG_KEY", Key: "org_key", Persistent: true, Description: "Organization enrollment key"},
	{Env: "HFL_NODE_ID", Key: "node_id", Persistent: true, Description: "Known node ID"},
	{Env: "HFL_INSTALLATION_ID", Key: "installation_id", Persistent: true, Description: "Current installation identity"},
	{Env: "HFL_INSTALLATION_MODE", Key: "installation_mode", Persistent: true, ReadOnly: true, Description: "Installer-owned Agent mode: system|user|account"},
	{Env: "HFL_RUN_AS_USER", Key: "run_as_user", Persistent: true, ReadOnly: true, Description: "Installer-owned selected ordinary account for continuous user protection"},
	{Env: "HFL_RUN_AS_HOME", Key: "run_as_home", Persistent: true, ReadOnly: true, Description: "Installer-owned profile root for the selected account"},
	{Env: "HFL_NODE_CREDENTIAL", Key: "node_token", Secret: true, Persistent: true, Description: "Long-lived node credential"},
	{Env: "HFL_NODE_TOKEN", Key: "node_token", Secret: true, Persistent: false, Description: "Legacy enrollment or installation-session credential"},
	{Env: "HFL_DATA_DIR", Key: "data_dir", Persistent: true, ReadOnly: true, Description: "Installer-owned Agent state directory"},
	{Env: "HFL_LOG_DIR", Key: "log_dir", Persistent: true, Description: "Rolling log directory"},
	{Env: "HFL_KOPIA_PATH", Key: "kopia_path", Persistent: true, Description: "Kopia CLI absolute path"},
	{Env: "HFL_BACKUP_SNAPSHOT_CONCURRENCY", Key: "backup_snapshot_concurrency", Persistent: true, Description: "Maximum concurrent prepared backup snapshots"},
	{Env: "HFL_NODE_ROLE", Key: "role", Persistent: true, ReadOnly: true, Description: "Enrollment-owned topology role: agent|proxy|gateway"},
}

func envByKey(key string) string {
	for _, f := range Registry {
		if f.Key == key && f.Persistent {
			return f.Env
		}
	}
	return ""
}

func fieldByEnv(env string) (Field, bool) {
	for _, f := range Registry {
		if f.Env == env {
			return f, true
		}
	}
	return Field{}, false
}
