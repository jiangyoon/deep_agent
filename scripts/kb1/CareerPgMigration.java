import java.io.Console;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.*;
import java.util.Arrays;
import java.util.Properties;

class CareerPgMigration {
    private static final String[] TABLES = {
        "file_assets", "sources", "sections", "attachments", "shared_rules",
        "rewards", "reward_rules", "mappings", "schedules", "issues"
    };

    private static void report(Connection connection, String sql) throws SQLException {
        try (var statement = connection.createStatement(); var rows = statement.executeQuery(sql)) {
            int columns = rows.getMetaData().getColumnCount();
            while (rows.next()) {
                for (int column = 1; column <= columns; column++) {
                    if (column > 1) System.out.print(" | ");
                    System.out.print(rows.getString(column));
                }
                System.out.println();
            }
        }
    }

    private static void probe(Connection connection) throws SQLException {
        report(connection, "SELECT current_database(), current_user, current_setting('server_version'), current_setting('server_encoding'), has_database_privilege(current_database(), 'CREATE')");
        report(connection, "SELECT datname, has_database_privilege(datname, 'CONNECT') FROM pg_database WHERE datname IN ('gyl_db','career') ORDER BY datname");
        report(connection, "SELECT nspname, has_schema_privilege(oid,'USAGE'), has_schema_privilege(oid,'CREATE') FROM pg_namespace WHERE nspname IN ('career','public') ORDER BY nspname");
        report(connection, "SELECT n.nspname,c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='career' OR (n.nspname='public' AND c.relname LIKE 'kb1%') ORDER BY 1,2");
    }

    private static void runFile(Connection connection, Path file) throws Exception {
        try (var statement = connection.createStatement()) {
            for (String sql : Files.readString(file, StandardCharsets.UTF_8).split(";\\s*(?=\\r?\\n|$)")) {
                if (!sql.isBlank()) statement.execute(sql);
            }
        }
    }

    private static void addCreatedAt(Connection connection) throws SQLException {
        try (var statement = connection.createStatement();
             var database = statement.executeQuery("SELECT current_database()")) {
            if (!database.next() || !"career".equals(database.getString(1))) {
                throw new SQLException("Expected independent career database");
            }
        }
        try (var statement = connection.createStatement()) {
            statement.execute("SET LOCAL lock_timeout = '5s'");
            statement.execute("SET LOCAL statement_timeout = '40s'");
            statement.execute("SELECT pg_advisory_xact_lock(hashtext('career-kb1-postgresql-import'))");
            for (String table : TABLES) {
                String name = "kb1_" + table;
                statement.execute("ALTER TABLE public." + name +
                    " ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP");
                statement.execute("COMMENT ON COLUMN public." + name +
                    ".created_at IS '记录入库时间；早于本列的历史数据以补列时间为准'");
                try (var definition = statement.executeQuery(
                    "SELECT data_type, is_nullable, column_default FROM information_schema.columns " +
                    "WHERE table_schema='public' AND column_name='created_at' AND table_name='" + name + "'")) {
                    if (!definition.next() || !"timestamp with time zone".equals(definition.getString(1)) ||
                        !"NO".equals(definition.getString(2)) || !"CURRENT_TIMESTAMP".equals(definition.getString(3))) {
                        throw new SQLException("Unexpected created_at definition for " + name);
                    }
                }
                try (var counts = statement.executeQuery(
                    "SELECT count(*), count(created_at) FROM public." + name)) {
                    if (!counts.next() || counts.getLong(1) != counts.getLong(2)) {
                        throw new SQLException("Incomplete created_at for " + name);
                    }
                    System.out.println(name + " created_at=" + counts.getLong(2));
                }
            }
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 3 || args.length > 4) throw new IllegalArgumentException("probe|migrate|verify|add-created-at JDBC_URL USER [PACKAGE]");
        Console console = System.console();
        if (console == null) throw new IllegalStateException("Run in a terminal that supports hidden password input");
        var password = console.readPassword("PostgreSQL password: ");
        if (password == null) throw new IllegalStateException("Missing password");
        var properties = new Properties();
        properties.setProperty("user", args[2]);
        properties.setProperty("password", new String(password));
        Arrays.fill(password, '\0');
        properties.setProperty("connectTimeout", "8");
        properties.setProperty("socketTimeout", "45");
        try (var connection = DriverManager.getConnection(args[1], properties)) {
            if (args[0].equals("probe") && args.length == 3) {
                probe(connection);
                return;
            }
            if (args[0].equals("add-created-at") && args.length == 3) {
                connection.setAutoCommit(false);
                try {
                    addCreatedAt(connection);
                    connection.commit();
                } catch (Exception failure) {
                    connection.rollback();
                    throw failure;
                }
                System.out.println("POSTGRES_CREATED_AT_COMMITTED");
                return;
            }
            if (args.length != 4 || !(args[0].equals("migrate") || args[0].equals("verify"))) {
                throw new IllegalArgumentException("Invalid operation");
            }
            var directory = Path.of(args[3]);
            connection.setAutoCommit(false);
            try {
                if (args[0].equals("migrate")) runFile(connection, directory.resolve("load.sql"));
                runFile(connection, directory.resolve("verify.sql"));
                connection.commit();
            } catch (Exception failure) {
                connection.rollback();
                throw failure;
            }
            System.out.println(args[0].equals("migrate") ? "POSTGRES_MIGRATION_COMMITTED" : "POSTGRES_DATA_VERIFIED");
            probe(connection);
        }
    }
}
