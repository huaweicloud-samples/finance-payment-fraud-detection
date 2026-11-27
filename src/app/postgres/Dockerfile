FROM postgres:16

# 安装 pgvector 扩展（向量相似度检索）
RUN apt update -y \
    && apt install -y postgresql-common ca-certificates curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && sh -c 'echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list' \
    && curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo -ne "\n" | /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh \
    && apt update -y \
    && apt install -y postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*
