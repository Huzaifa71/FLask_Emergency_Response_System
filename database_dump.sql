BEGIN TRANSACTION;
CREATE TABLE employees 
                     (phone TEXT PRIMARY KEY, info TEXT);
INSERT INTO "employees" VALUES('03463259079','{"Name": "Mudassir Hussain", "Department": "IT", "Shift": "Morning"}');
INSERT INTO "employees" VALUES('03198135551','{"Name": "a", "Department": "HR", "Shift": "Evening"}');
INSERT INTO "employees" VALUES('01223334444','{"Name": "b", "Department": "Finance", "Shift": "Night"}');
CREATE TABLE poll_results 
                     (phone TEXT, response TEXT, timestamp TEXT,
                      PRIMARY KEY (phone));
INSERT INTO "poll_results" VALUES('03198135551','I am okay and safe.','2026-02-04 13:56:57');
INSERT INTO "poll_results" VALUES('03463259079','I am stuck and help is needed.','2026-02-04 14:22:00');
INSERT INTO "poll_results" VALUES('01223334444','I am stuck but help not needed.','2026-02-04 14:50:26');
CREATE TABLE settings
                     (key TEXT PRIMARY KEY, value TEXT);
INSERT INTO "settings" VALUES('poll_start','2026-01-01 09:00:00');
INSERT INTO "settings" VALUES('poll_end','2026-02-27 18:00:00');
INSERT INTO "settings" VALUES('validation_mode','flexible');
INSERT INTO "settings" VALUES('timezone','Asia/Karachi');
INSERT INTO "settings" VALUES('time_format','12');
INSERT INTO "settings" VALUES('col_phone','Phone');
CREATE INDEX idx_timestamp 
                     ON poll_results(timestamp);
COMMIT;
