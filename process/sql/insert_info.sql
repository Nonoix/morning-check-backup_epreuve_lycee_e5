INSERT INTO mcb_info (id_pipeline, server_name, backup_sessions, backup_total, backup_success, backup_warning,
                      backup_failed, backup_running, backup_pending, backup_idle, backup_undefined, backup_in_progress,
                      tape_sessions, tape_success, tape_warning, tape_failed, tape_running, tape_pending, tape_idle,
                      tape_undefined, tape_in_progress, repositories)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);