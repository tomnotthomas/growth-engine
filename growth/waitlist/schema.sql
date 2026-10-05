-- Waitlist with double opt-in and referrals (Cloudflare D1, SQLite dialect).
-- Only hashes of the private tokens are stored; the tokens themselves exist only in the emails.

CREATE TABLE IF NOT EXISTS signups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL CHECK (role IN ('player', 'host')),
  lang TEXT NOT NULL,
  code TEXT NOT NULL UNIQUE,
  status_hash TEXT UNIQUE,
  confirm_hash TEXT UNIQUE,
  referred_by INTEGER REFERENCES signups(id) ON DELETE SET NULL,
  referrals INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL,
  page TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  mail_sent_at TEXT,
  confirmed_at TEXT,
  moved_up_mail_at TEXT
);
CREATE INDEX IF NOT EXISTS signups_confirmed ON signups (role, confirmed_at);
CREATE INDEX IF NOT EXISTS signups_referred_by ON signups (referred_by);

-- Sign-up attempts per hashed IP and hour, so the form can't be used to mail-bomb strangers.
CREATE TABLE IF NOT EXISTS ip_hits (
  ip_hash TEXT NOT NULL,
  hour TEXT NOT NULL,
  n INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (ip_hash, hour)
);
