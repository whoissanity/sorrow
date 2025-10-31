PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS guild_config (
  guild_id            INTEGER PRIMARY KEY,
  prefix              TEXT NOT NULL DEFAULT ',',
  vm_category_id      INTEGER,
  vm_interface_id     INTEGER,
  vm_jtc_id           INTEGER,
  vm_default_name     TEXT DEFAULT '{user.name} Channel',
  vm_default_bitrate  INTEGER DEFAULT 64000,
  vm_default_region   TEXT,
  vm_auto_role_id     INTEGER,
  raid_mode           INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS staff_roles (
  guild_id INTEGER NOT NULL,
  role_id  INTEGER NOT NULL,
  PRIMARY KEY (guild_id, role_id)
);

CREATE TABLE IF NOT EXISTS invoke_messages (
  guild_id     INTEGER NOT NULL,
  command_name TEXT NOT NULL,
  invoke_msg   TEXT,
  dm_msg       TEXT,
  PRIMARY KEY (guild_id, command_name)
);

CREATE TABLE IF NOT EXISTS warnings (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id   INTEGER NOT NULL,
  user_id    INTEGER NOT NULL,
  moderator  INTEGER NOT NULL,
  reason     TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);








CREATE TABLE IF NOT EXISTS temp_bans (
  guild_id INTEGER NOT NULL,
  user_id  INTEGER NOT NULL,
  unban_at TIMESTAMP NOT NULL,
  reason   TEXT,
  PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS vm_channels (
  guild_id   INTEGER NOT NULL,
  channel_id INTEGER PRIMARY KEY,
  owner_id   INTEGER NOT NULL,
  ghosted    INTEGER DEFAULT 0,
  locked     INTEGER DEFAULT 0,
  music_mode INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS booster_roles (
  guild_id INTEGER NOT NULL,
  user_id  INTEGER NOT NULL,
  role_id  INTEGER NOT NULL,
  PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS fake_perms (
  guild_id INTEGER NOT NULL,
  user_id  INTEGER NOT NULL,
  perm     TEXT NOT NULL,
  PRIMARY KEY (guild_id, user_id, perm)
);

-- Store original roles for hardmute so we can restore them with unhardmute
CREATE TABLE IF NOT EXISTS hardmute_store (
  guild_id INTEGER NOT NULL,
  user_id  INTEGER NOT NULL,
  roles    TEXT NOT NULL,       -- comma-separated role IDs
  PRIMARY KEY (guild_id, user_id)
);
-- Log channels for security (antinuke/antiraid) and mod/other
CREATE TABLE IF NOT EXISTS log_config (
  guild_id INTEGER PRIMARY KEY,
  security_log_channel_id INTEGER,
  mod_log_channel_id INTEGER
);

-- Tickets metadata
CREATE TABLE IF NOT EXISTS tickets (
  guild_id   INTEGER NOT NULL,
  channel_id INTEGER PRIMARY KEY,
  opener_id  INTEGER NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  closed     INTEGER NOT NULL DEFAULT 0,
  closed_by  INTEGER,
  close_reason TEXT,
  closed_at  TIMESTAMP
);

-- Fake perms (used by perm_or_fp)
CREATE TABLE IF NOT EXISTS fakeperms (
  guild_id INTEGER NOT NULL,
  user_id  INTEGER NOT NULL,
  perm     TEXT NOT NULL,
  PRIMARY KEY (guild_id, user_id, perm)
);

-- Warnings with stable IDs (for warnings embed)
CREATE TABLE IF NOT EXISTS warnings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  moderator INTEGER NOT NULL,
  reason TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Hardmute/stripstaff role snapshots
CREATE TABLE IF NOT EXISTS role_snapshots (
  guild_id INTEGER NOT NULL,
  user_id  INTEGER NOT NULL,
  roles    TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (guild_id, user_id)
);

-- VoiceMaster: temp channels ownership
CREATE TABLE IF NOT EXISTS vm_channels (
  guild_id INTEGER NOT NULL,
  channel_id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL
);

-- Autorole
CREATE TABLE IF NOT EXISTS autorole (
  guild_id INTEGER PRIMARY KEY,
  role_id INTEGER
);

-- Img-only channels
CREATE TABLE IF NOT EXISTS imgonly_channels (
  guild_id INTEGER NOT NULL,
  channel_id INTEGER NOT NULL,
  PRIMARY KEY (guild_id, channel_id)
);

-- Reminders
CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER,
  channel_id INTEGER,
  user_id INTEGER NOT NULL,
  fire_at TEXT NOT NULL,
  text TEXT NOT NULL
);

-- Lockdown ignore list
CREATE TABLE IF NOT EXISTS lockdown_ignore (
  guild_id INTEGER NOT NULL,
  channel_id INTEGER NOT NULL,
  PRIMARY KEY (guild_id, channel_id)
);