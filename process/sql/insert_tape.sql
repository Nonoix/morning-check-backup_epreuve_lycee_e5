INSERT INTO mcb_tape (id_info, start_date, end_date, backup_status, backup_status_details, job_name, job_id, reason,
                     mediapool_name)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);