SELECT
    br.*,

    h.name AS host_name, h.ip AS host_ip,

    br2.name as scale_out_name,

    bcr.totalSpace, bcr.freeSpace

FROM
    [dbo].BackupRepositories br

LEFT JOIN [dbo].Hosts h
    ON h.id = br.host_id

LEFT JOIN [dbo].[BackupRepositoryContainer.Repositories] cr
    ON cr.repositoryId = br.id

LEFT JOIN [dbo].[BackupRepositoryContainer] bcr
    ON bcr.id = cr.id

LEFT JOIN [dbo].[Backup.ExtRepo.ExtRepos] er
    ON er.extent_id = br.id

LEFT JOIN [dbo].[BackupRepositories] br2
    ON br2.id = er.meta_repo_id

WHERE
    br.type = 0
