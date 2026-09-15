-- ============================================================
-- GA家园系统 homestead.db schema v1（M0，2026-09-14）
-- 对应开发方案 §6 + §1.4 交换日记；SQLite 3.x
-- 约定：时间戳 = 本地时间字符串 "YYYY-MM-DD HH:MM:SS"
-- 幂等：全部 IF NOT EXISTS，可重复执行
-- ============================================================

-- ===== 账号体系 =====
CREATE TABLE IF NOT EXISTS actors (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL UNIQUE,
  type        TEXT NOT NULL CHECK (type IN ('character','npc','master','mephisto')),
  persona_ref TEXT,                -- 角色档案文件相对路径
  avatar      TEXT,                -- 头像资源路径（M4 前可为空）
  created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS masks (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_id       INTEGER NOT NULL REFERENCES actors(id),
  forum_username TEXT NOT NULL UNIQUE,   -- 对照关系仅生成层可见，展示层永不暴露
  created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ===== 朋友圈 =====
CREATE TABLE IF NOT EXISTS posts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_id    INTEGER NOT NULL REFERENCES actors(id),
  content     TEXT NOT NULL,
  images_json TEXT NOT NULL DEFAULT '[]',  -- M4 前纯文字+emoji
  created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  gen_task_id TEXT
);

CREATE TABLE IF NOT EXISTS comments (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id    INTEGER NOT NULL REFERENCES posts(id),
  actor_id   INTEGER NOT NULL REFERENCES actors(id),
  content    TEXT NOT NULL,
  parent_id  INTEGER REFERENCES comments(id),  -- 楼中楼
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS likes (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id    INTEGER NOT NULL REFERENCES posts(id),
  actor_id   INTEGER NOT NULL REFERENCES actors(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  UNIQUE (post_id, actor_id)
);

-- ===== 留言板（M1）=====
CREATE TABLE IF NOT EXISTS notes (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_id   INTEGER NOT NULL REFERENCES actors(id),
  content    TEXT NOT NULL,
  color      TEXT NOT NULL DEFAULT 'yellow',
  pos_x      REAL NOT NULL DEFAULT 0.5,   -- 便签墙相对坐标
  pos_y      REAL NOT NULL DEFAULT 0.5,
  pinned     INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS note_replies (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id    INTEGER NOT NULL REFERENCES notes(id),
  actor_id   INTEGER NOT NULL REFERENCES actors(id),
  content    TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ===== 交换日记（M1.5，§1.4）=====
CREATE TABLE IF NOT EXISTS diary_entries (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_id   INTEGER NOT NULL REFERENCES actors(id),   -- 哪个角色的日记本
  content    TEXT NOT NULL,
  mood       TEXT,                                     -- 轻量心情标签，可空
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS diary_replies (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id   INTEGER NOT NULL REFERENCES diary_entries(id),
  author_id  INTEGER NOT NULL REFERENCES actors(id),   -- 主人或角色写的
  content    TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ===== 论坛（M3）=====
CREATE TABLE IF NOT EXISTS boards (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  key         TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  description TEXT,
  access      TEXT NOT NULL DEFAULT 'public' CHECK (access IN ('public','secret'))  -- secret=N109暗号
);

CREATE TABLE IF NOT EXISTS threads (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  board_id       INTEGER NOT NULL REFERENCES boards(id),
  author_mask_id INTEGER NOT NULL REFERENCES masks(id),
  title          TEXT NOT NULL,
  content        TEXT NOT NULL,
  is_pinned      INTEGER NOT NULL DEFAULT 0,
  views          INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS thread_posts (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id      INTEGER NOT NULL REFERENCES threads(id),
  author_mask_id INTEGER NOT NULL REFERENCES masks(id),
  content        TEXT NOT NULL,
  created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ===== 生成审计与防重复（§7.2）=====
CREATE TABLE IF NOT EXISTS gen_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id      TEXT NOT NULL,
  actor_id     INTEGER REFERENCES actors(id),
  action       TEXT NOT NULL,          -- post|comment|like|note|diary|thread|...
  result_ref   TEXT,                   -- 产出行的 表:ID
  content_hash TEXT,                   -- 与近30天对比，过似丢弃
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS unread_interactions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_id   INTEGER NOT NULL REFERENCES actors(id),   -- 该谁"看到"
  kind       TEXT NOT NULL,           -- master_comment|master_reply|master_note_reply|master_diary_reply
  ref_id     INTEGER NOT NULL,        -- 指向 comments/notes/diary_replies 等行
  status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','skipped')),
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ===== 常用索引 =====
CREATE INDEX IF NOT EXISTS idx_posts_actor      ON posts(actor_id, created_at);
CREATE INDEX IF NOT EXISTS idx_comments_post    ON comments(post_id, parent_id);
CREATE INDEX IF NOT EXISTS idx_notes_created    ON notes(created_at);
CREATE INDEX IF NOT EXISTS idx_diary_actor      ON diary_entries(actor_id, created_at);
CREATE INDEX IF NOT EXISTS idx_diary_replies    ON diary_replies(entry_id, created_at);
CREATE INDEX IF NOT EXISTS idx_threads_board    ON threads(board_id, is_pinned DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_genlog_hash      ON gen_log(content_hash, created_at);
CREATE INDEX IF NOT EXISTS idx_unread_status    ON unread_interactions(status, actor_id);
