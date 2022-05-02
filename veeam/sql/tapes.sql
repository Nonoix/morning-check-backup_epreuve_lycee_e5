SELECT
    js.*,

    tp.full_mediapool_id,

    mp.name AS mediapool_name
FROM
    [dbo].[Backup.Model.JobSessions] js

LEFT JOIN [dbo].[Tape.jobs] tp
    ON js.job_id = tp.id

LEFT JOIN [dbo].[Tape.media_pools] mp
    ON tp.full_mediapool_id = mp.id

LEFT JOIN [dbo].[Bjobs] bj
    ON bj.id = js.job_id
WHERE
    js.job_type = 28
    AND bj.is_deleted = 0
    AND bj.schedule_enabled = 1
ORDER BY
    js.creation_time DESC;
