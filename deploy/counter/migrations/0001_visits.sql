CREATE TABLE visit_total (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  total INTEGER NOT NULL CHECK (total >= 0)
);

INSERT INTO visit_total (id, total) VALUES (1, 0);

CREATE TABLE visits (
  id TEXT PRIMARY KEY NOT NULL
) WITHOUT ROWID;

CREATE TRIGGER visit_added
AFTER INSERT ON visits
BEGIN
  UPDATE visit_total SET total = total + 1 WHERE id = 1;
END;
