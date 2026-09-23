-- Run once against the existing database before starting the updated server.
USE ctf_platform;
CREATE TABLE teams (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(80) NOT NULL UNIQUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE users
    ADD COLUMN is_active TINYINT(1) NOT NULL DEFAULT 1,
    ADD COLUMN team_id BIGINT UNSIGNED NULL,
    ADD CONSTRAINT fk_users_team FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE SET NULL;
-- Different registered users may share a nickname; user_id identifies a solve.
ALTER TABLE solves DROP INDEX uq_challenge_nickname;
INSERT INTO settings (setting_key, setting_value) VALUES
('competition_mode', 'individual'),
('scoreboard_hidden_from', NULL),
('scoreboard_hidden_until', NULL)
ON DUPLICATE KEY UPDATE setting_key = VALUES(setting_key);
