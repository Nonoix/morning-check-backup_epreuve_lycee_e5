INSERT INTO mcb_failed (id_info, start_date, end_date, session_id, orig_session_id, backup_status,
                        backup_status_details, last_point_success, object_id, job_name, job_id, type, reason,
                        object_name, backup_transport_mode, target_storage, proxies, nb_restore_points, retaindays,
                        retaincycles, retention_maintenance)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);