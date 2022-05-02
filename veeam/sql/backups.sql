SELECT bts.*,

       js.job_id,
       js.job_name,
       js.job_type,
       js.orig_session_id,

       bj.description,
       bj.repository_id,
       bj.schedule    as job_schedule,
       bj.options,
       bj.job_source_type,
       bj.description as job_description,

       br.name        as repository_name,

       bo.type        as object_type,
       bo.platform    as object_platform,
       bo.viobject_type,

       (
           SELECT TOP 1
            oib.creation_time
           FROM
               [dbo].[Backup.Model.OIBs] oib
           WHERE
               oib.object_id = bts.object_id
             AND oib.completion_time_utc IS NOT null
           ORDER BY
               oib.creation_time DESC
       )              as last_point_success,

       (
           SELECT COUNT(*)
           FROM
               [dbo].[Backup.Model.OIBs] oib
           WHERE
               oib.storage_id IN (
               SELECT
               id
               FROM
               [dbo].[Backup.Model.Storages] s
               WHERE
               s.backup_id IN (
               SELECT
               id
               FROM
               [dbo].[Backup.Model.Backups] b
               WHERE
               b.job_id = js.job_id
               )
               )
             AND oib.object_id = bts.object_id
             AND oib.completion_time_utc IS NOT null
       )              as nb_restore_points

FROM
    [dbo].[Backup.Model.BackupTaskSessions] AS bts
    LEFT JOIN [dbo].[Backup.Model.JobSessions] AS js
ON js.id = bts.session_id
    LEFT JOIN [dbo].[BJobs] AS bj
    ON bj.id = js.job_id
    LEFT JOIN [dbo].[BackupRepositories] AS br
    ON br.id = bj.repository_id
    LEFT JOIN [dbo].[BObjects] AS bo
    ON bo.id = bts.object_id
WHERE
    bts.creation_time BETWEEN '{0}'
  AND '{1}'
  AND js.job_type = 0
  AND bo.viobject_type != 'Vapp'
  AND bo.type != 4
ORDER BY
    bts.creation_time ASC;
