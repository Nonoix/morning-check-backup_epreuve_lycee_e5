create table mcb_pipeline
(
    id                 int unsigned auto_increment,
    id_pipeline_gitlab int unsigned  not null,
    creation_time      datetime     not null,
    comment            text null,
    constraint mcb_pipeline_pk
        primary key (id)
);

create table mcb_info
(
    id                   int unsigned auto_increment,
    id_pipeline          int unsigned not null,
    server_name          text not null,
    backup_sessions      int unsigned         null,
    backup_total         int unsigned         null,
    backup_success       int unsigned         null,
    backup_warning       int unsigned         null,
    backup_failed        int unsigned         null,
    backup_running       int unsigned         null,
    backup_pending       int unsigned         null,
    backup_idle          int unsigned         null,
    backup_undefined     int unsigned         null,
    backup_in_progress   int unsigned         null,
    tape_sessions        int unsigned         null,
    tape_success         int unsigned         null,
    tape_warning         int unsigned         null,
    tape_failed          int unsigned         null,
    tape_running         int unsigned         null,
    tape_pending         int unsigned         null,
    tape_idle            int unsigned         null,
    tape_undefined       int unsigned         null,
    tape_in_progress     int unsigned         null,
    repositories         int unsigned         null,
    constraint mcb_info_pk
        primary key (id),
    constraint mcb_info_mcb_pipeline_id_fk
        foreign key (id_pipeline) references mcb_pipeline (id)
            on update cascade on delete cascade
);

create table mcb_tape
(
    id                    int unsigned auto_increment,
    id_info              int unsigned          not null,
    start_date            datetime     not null,
    end_date              datetime     null,
    backup_status         int          not null,
    backup_status_details text  not null,
    job_name              text not null,
    job_id                varchar(40) not null,
    reason                text   null,
    mediapool_name        text not null,
    constraint mcb_tape_pk
        primary key (id),
    constraint mcb_tape_mcb_info_id_fk
        foreign key (id_info) references mcb_info (id)
            on update cascade on delete cascade
);

create table mcb_in_progress
(
    id                    int unsigned auto_increment,
    id_info              int unsigned          not null,
    start_date            datetime     not null,
    session_id            varchar(40) not null,
    orig_session_id       varchar(40) not null,
    backup_status         int          not null,
    backup_status_details text  not null,
    last_point_success    datetime     null,
    object_id             varchar(40) not null,
    job_name              text not null,
    job_id                varchar(40) not null,
    type                  text  not null,
    object_name           text not null,
    backup_transport_mode varchar(10)  null,
    target_storage        text not null,
    proxies               text null,
    nb_restore_points     smallint unsigned not null,
    retaindays            smallint unsigned not null,
    retaincycles          smallint unsigned not null,
    retention_maintenance boolean      null,
    constraint mcb_in_progress_pk
        primary key (id),
    constraint mcb_in_progress_mcb_info_id_fk
        foreign key (id_info) references mcb_info (id)
            on update cascade on delete cascade
);

create table mcb_failed
(
    id                    int unsigned auto_increment,
    id_info              int unsigned         not null,
    start_date            datetime    not null,
    end_date              datetime    not null,
    session_id            varchar(40) not null,
    orig_session_id       varchar(40) not null,
    backup_status         int          not null,
    backup_status_details text  not null,
    last_point_success    datetime     null,
    object_id             varchar(40) not null,
    job_name              text not null,
    job_id                varchar(40) not null,
    type                  text  not null,
    reason                text   not null,
    object_name           text not null,
    backup_transport_mode varchar(10)  null,
    target_storage        text not null,
    proxies               text null,
    nb_restore_points     smallint unsigned not null,
    retaindays            smallint unsigned not null,
    retaincycles          smallint unsigned not null,
    retention_maintenance boolean      null,

    constraint mcb_failed_pk
        primary key (id),
    constraint mcb_failed_mcb_info_id_fk
        foreign key (id_info) references mcb_info (id)
            on update cascade on delete cascade
);

create table mcb_repositorie
(
    id             int unsigned auto_increment,
    id_info       int unsigned          not null,
    id_repo        varchar(40)  not null,
    name           text not null,
    extent         text null,
    description    text   null,
    type           int unsigned          not null,
    path           text   not null,
    status         int          not null,
    host_name      text not null,
    host_ip        varchar(15)          null,
    scale_out_name text null,
    free           bigint unsigned not null,
    total          bigint unsigned not null,
    used           bigint unsigned not null,
    constraint mcb_repositorie_pk
        primary key (id),
    constraint mcb_repositorie_mcb_info_id_fk
        foreign key (id_info) references mcb_info (id)
            on update cascade on delete cascade
);