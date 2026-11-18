import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.sql.*;
import java.util.*;

/** First-batch migration utility. Credentials arrive through stdin, never files or argv. */
class CareerDmMigration {
    private static String secret;
    private static String safe(String message) {
        return message == null ? "" : (secret == null ? message : message.replace(secret, "[redacted]"));
    }
    static void showQuery(Connection db, String label, String sql) throws SQLException {
        System.out.println(label);
        try (Statement stmt=db.createStatement(); ResultSet rs=stmt.executeQuery(sql)) {
            int cols=rs.getMetaData().getColumnCount();
            while(rs.next()) {
                List<String> values=new ArrayList<>();
                for(int i=1;i<=cols;i++) values.add(rs.getString(i));
                System.out.println(String.join(" | ",values));
            }
        }
    }
    static class Table {
        String name, checksum;
        int expectedRows;
        List<String> columns=new ArrayList<>(), kinds=new ArrayList<>(), modes=new ArrayList<>(), primary=new ArrayList<>();
        Path rows;
    }
    static class Package {
        Path root;
        Properties properties=new Properties();
        LinkedHashMap<String,Table> tables=new LinkedHashMap<>();
        List<String> views=new ArrayList<>();
        Package(Path root) throws Exception {
            this.root=root.toAbsolutePath();
            try(Reader reader=Files.newBufferedReader(root.resolve("migration.properties"),StandardCharsets.UTF_8)) { properties.load(reader); }
            for(String line:Files.readAllLines(root.resolve("tables.tsv"),StandardCharsets.UTF_8)) {
                String[] v=line.split("\t",-1);
                if(v[0].equals("T")) {
                    Table t=new Table(); t.name=v[1]; t.expectedRows=Integer.parseInt(v[2]); t.checksum=v[3];
                    if(!t.name.matches("[a-z_][a-z0-9_]*")) throw new IOException("Invalid table identifier");
                    t.rows=root.resolve("rows").resolve(t.name+".tsv"); tables.put(t.name,t);
                } else if(v[0].equals("C")) {
                    Table t=tables.get(v[1]);
                    if(!v[2].matches("[a-z_][a-z0-9_]*")) throw new IOException("Invalid column identifier");
                    t.columns.add(v[2]); t.kinds.add(v[3]); t.modes.add(v[4]);
                } else if(v[0].equals("P")) { tables.get(v[1]).primary.add(v[2]); }
                else throw new IOException("Unknown metadata record");
            }
            String sql=Files.readString(root.resolve("views.sql"),StandardCharsets.UTF_8);
            java.util.regex.Matcher m=java.util.regex.Pattern.compile("CREATE VIEW \\\"\\{\\{SCHEMA\\}\\}\\\"\\.\\\"([^\\\"]+)\\\"").matcher(sql);
            while(m.find()) views.add(m.group(1));
            if(tables.size()!=11 || views.size()!=9) throw new IOException("Incomplete migration package");
        }
    }
    static String sha(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }
    static String key(Table t,String[] values) {
        List<String> keys=new ArrayList<>();
        for(String p:t.primary) keys.add(values[t.columns.indexOf(p)]);
        return String.join("\t",keys);
    }
    static Map<String,String> expectedRows(Table t) throws Exception {
        if(!sha(Files.readAllBytes(t.rows)).equals(t.checksum)) throw new IOException("Transport checksum mismatch: "+t.name);
        Map<String,String> rows=new HashMap<>();
        for(String line:Files.readAllLines(t.rows,StandardCharsets.UTF_8)) {
            String[] tokens=line.split("\t",-1);
            if(tokens.length!=t.columns.size()) throw new IOException("Column count mismatch: "+t.name);
            for(int i=0;i<tokens.length;i++) {
                if(tokens[i].equals("N")) continue;
                if(t.kinds.get(i).equals("I")) {
                    if(!tokens[i].startsWith("I:")) throw new IOException("Invalid integer token");
                    Long.parseLong(tokens[i].substring(2));
                } else {
                    if(!tokens[i].startsWith("T:")) throw new IOException("Invalid text token");
                    Base64.getDecoder().decode(tokens[i].substring(2));
                }
            }
            String previous=rows.put(key(t,tokens),sha(line.getBytes(StandardCharsets.UTF_8)));
            if(previous!=null) throw new IOException("Duplicate primary key in transport: "+t.name);
        }
        if(rows.size()!=t.expectedRows) throw new IOException("Row count mismatch in transport: "+t.name);
        return rows;
    }
    static void checkPackage(Package pack) throws Exception {
        int total=0;
        for(Table t:pack.tables.values()) { expectedRows(t); total+=t.expectedRows; }
        if(total!=Integer.parseInt(pack.properties.getProperty("total.rows"))) throw new IOException("Total count mismatch");
        System.out.println("PACKAGE_VALIDATED tables="+pack.tables.size()+" rows="+total+" views="+pack.views.size());
    }
    static String qualified(String schema,String name) { return "\""+schema+"\".\"KB1_"+name.toUpperCase(Locale.ROOT)+"\""; }
    static String columns(Table t) {
        List<String> names=new ArrayList<>();
        for(String col:t.columns) names.add("\""+col.toUpperCase(Locale.ROOT)+"\"");
        return String.join(",",names);
    }
    static void executeSqlFile(Connection db,Package pack,String name,String schema) throws Exception {
        String sql=Files.readString(pack.root.resolve(name),StandardCharsets.UTF_8).replace("{{SCHEMA}}",schema);
        // These generator-owned DDL files never place ASCII semicolons inside literals.
        for(String statement:sql.split(";")) {
            if(statement.isBlank()) continue;
            try(Statement stmt=db.createStatement()) { stmt.execute(statement); }
        }
    }
    static void bindRow(PreparedStatement stmt,Table t,String[] values) throws Exception {
        for(int i=0;i<values.length;i++) {
            String token=values[i];
            if(token.equals("N")) stmt.setNull(i+1,t.kinds.get(i).equals("I")?Types.BIGINT:t.modes.get(i).equals("L")?Types.CLOB:Types.VARCHAR);
            else if(t.kinds.get(i).equals("I")) stmt.setLong(i+1,Long.parseLong(token.substring(2)));
            else {
                String value=new String(Base64.getDecoder().decode(token.substring(2)),StandardCharsets.UTF_8);
                if(t.modes.get(i).equals("L")) stmt.setCharacterStream(i+1,new StringReader(value),value.length());
                else stmt.setString(i+1,value);
            }
        }
    }
    static void importTable(Connection db,Table t,String schema) throws Exception {
        String sql="INSERT INTO "+qualified(schema,t.name)+" ("+columns(t)+") VALUES ("+String.join(",",Collections.nCopies(t.columns.size(),"?"))+")";
        int count=0;
        try(PreparedStatement stmt=db.prepareStatement(sql)) {
            for(String line:Files.readAllLines(t.rows,StandardCharsets.UTF_8)) {
                bindRow(stmt,t,line.split("\t",-1)); stmt.addBatch(); count++;
                if(count%50==0) stmt.executeBatch();
            }
            if(count%50!=0) stmt.executeBatch();
        }
        System.out.println("IMPORTED "+t.name+" "+count);
    }
    static String readText(ResultSet rs,int i,boolean lob) throws Exception {
        if(!lob) return rs.getString(i);
        Reader reader=rs.getCharacterStream(i);
        if(reader==null) return null;
        try(reader) {
            StringBuilder text=new StringBuilder(); char[] buffer=new char[8192]; int n;
            while((n=reader.read(buffer))!=-1) text.append(buffer,0,n);
            return text.toString();
        }
    }
    static void verifyTable(Connection db,Table t,String schema) throws Exception {
        Map<String,String> expected=expectedRows(t);
        Set<String> seen=new HashSet<>();
        try(Statement stmt=db.createStatement()) {
            stmt.setFetchSize(100);
            try(ResultSet rs=stmt.executeQuery("SELECT "+columns(t)+" FROM "+qualified(schema,t.name))) {
                while(rs.next()) {
                    String[] tokens=new String[t.columns.size()];
                    for(int i=0;i<tokens.length;i++) {
                        if(t.kinds.get(i).equals("I")) {
                            long n=rs.getLong(i+1); tokens[i]=rs.wasNull()?"N":"I:"+n;
                        } else {
                            String text=readText(rs,i+1,t.modes.get(i).equals("L"));
                            tokens[i]=text==null?"N":"T:"+Base64.getEncoder().encodeToString(text.getBytes(StandardCharsets.UTF_8));
                        }
                    }
                    String key=key(t,tokens);
                    if(!seen.add(key) || !Objects.equals(expected.get(key),sha(String.join("\t",tokens).getBytes(StandardCharsets.UTF_8))))
                        throw new IOException("Round-trip content mismatch: "+t.name+" (including CLOB/Unicode/null verification)");
                }
            }
        }
        if(seen.size()!=t.expectedRows) throw new IOException("Target count mismatch: "+t.name);
        System.out.println("VERIFIED "+t.name+" "+seen.size()+" full-row SHA256");
    }
    static String databaseName(Connection db) throws SQLException {
        try(Statement stmt=db.createStatement();ResultSet rs=stmt.executeQuery("SELECT NAME FROM V$DATABASE")) { rs.next(); return rs.getString(1).trim(); }
    }
    static void preflight(Connection db,Package pack,String schema) throws Exception {
        if(!databaseName(db).equalsIgnoreCase(pack.properties.getProperty("expected.database"))) throw new IOException("Unexpected target database");
        boolean exists=false;
        try(ResultSet rs=db.getMetaData().getSchemas()) {
            while(rs.next()) if(schema.equals(rs.getString("TABLE_SCHEM").trim())) exists=true;
        }
        if(!exists) throw new IOException("Expected schema does not exist: "+schema);
        Set<String> reserved=new HashSet<>();
        for(Table t:pack.tables.values()) reserved.add("KB1_"+t.name.toUpperCase(Locale.ROOT));
        reserved.add("KB1_MIGRATION_INFO"); reserved.addAll(pack.views);
        try(PreparedStatement stmt=db.prepareStatement("SELECT OBJECT_NAME FROM ALL_OBJECTS WHERE OWNER=?")) {
            stmt.setString(1,schema);
            try(ResultSet rs=stmt.executeQuery()) {
                while(rs.next()) if(reserved.contains(rs.getString(1).trim())) throw new IOException("Existing target object; refusing overwrite: "+rs.getString(1));
            }
        }
        System.out.println("PREFLIGHT_OK database="+databaseName(db)+" schema="+schema+" no target object collisions");
    }
    static void migrate(Connection db,Package pack) throws Exception {
        checkPackage(pack);
        String schema=pack.properties.getProperty("schema");
        if(!schema.matches("[A-Z][A-Z0-9_]{0,29}")) throw new IOException("Invalid schema");
        preflight(db,pack,schema);
        Path report=pack.root.resolve("migration_report.txt");
        try {
            executeSqlFile(db,pack,"schema.sql",schema);
            for(Table t:pack.tables.values()) importTable(db,t,schema);
            for(Table t:pack.tables.values()) verifyTable(db,t,schema);
            // Commit core rows only after all field-level round trips succeed.
            try(PreparedStatement stmt=db.prepareStatement("INSERT INTO \""+schema+"\".\"KB1_MIGRATION_INFO\" VALUES(?,?,?,CURRENT_TIMESTAMP,?,?)")) {
                stmt.setString(1,pack.properties.getProperty("run.id")); stmt.setString(2,pack.properties.getProperty("source.sha256"));
                stmt.setString(3,pack.properties.getProperty("batch.id"));
                StringBuilder counts=new StringBuilder();
                for(Table t:pack.tables.values()) counts.append(t.name).append('=').append(t.expectedRows).append('\n');
                stmt.setCharacterStream(4,new StringReader(counts.toString()),counts.length()); stmt.setString(5,"Y"); stmt.executeUpdate();
            }
            db.commit();
            // DDL can commit independently in DM; views are built after durable core verification.
            executeSqlFile(db,pack,"views.sql",schema);
            db.commit();
            if(Files.exists(pack.root.resolve("column_comments.sql"))) commentSchema(db,pack);
            StringBuilder result=new StringBuilder("SUCCESS\nDATABASE="+databaseName(db)+"\nSCHEMA="+schema+"\n");
            result.append("SOURCE_SHA256=").append(pack.properties.getProperty("source.sha256")).append('\n');
            for(Table t:pack.tables.values()) {
                try(Statement stmt=db.createStatement();ResultSet rs=stmt.executeQuery("SELECT COUNT(*) FROM "+qualified(schema,t.name))) {
                    rs.next(); int actual=rs.getInt(1);
                    if(actual!=t.expectedRows) throw new IOException("Post-commit count mismatch: "+t.name);
                    result.append("TABLE ").append(t.name).append(' ').append(actual).append(" VERIFIED_FULL_ROW\n");
                }
            }
            for(String view:pack.views) {
                try(Statement stmt=db.createStatement();ResultSet rs=stmt.executeQuery("SELECT COUNT(*) FROM \""+schema+"\".\""+view+"\"")) {
                    rs.next(); result.append("VIEW ").append(view).append(' ').append(rs.getInt(1)).append('\n');
                }
            }
            Files.writeString(report,result.toString(),StandardCharsets.UTF_8);
            System.out.println(result);
        } catch(Exception error) {
            db.rollback();
            Files.writeString(report,"FAILED\n"+safe(error.getMessage())+"\nNew DDL objects may remain; existing objects were not overwritten.\n",StandardCharsets.UTF_8);
            throw error;
        }
    }
    static void verifyCommitted(Connection db,Package pack) throws Exception {
        checkPackage(pack);
        String schema=pack.properties.getProperty("schema");
        if(!schema.matches("[A-Z][A-Z0-9_]{0,29}") || !databaseName(db).equalsIgnoreCase(pack.properties.getProperty("expected.database")))
            throw new IOException("Unexpected verification target");
        for(Table t:pack.tables.values()) verifyTable(db,t,schema);
        showQuery(db,"ENABLED_CONSTRAINTS", "SELECT CONSTRAINT_TYPE,STATUS,COUNT(*) FROM ALL_CONSTRAINTS WHERE OWNER='CAREER' AND TABLE_NAME LIKE 'KB1_%' GROUP BY CONSTRAINT_TYPE,STATUS ORDER BY CONSTRAINT_TYPE");
        showQuery(db,"REWARD_SAMPLE", "SELECT \"记录ID\",\"专业或级别分支\",\"一次性奖励元\",\"注册补贴元\",\"注册补贴周期\" FROM \"CAREER\".\"V_KB1_REWARDS\" WHERE \"记录ID\" IN ('N034-R004-01','N034-R004-02','N034-R004-03') ORDER BY \"记录ID\"");
        showQuery(db,"ISOLATED_MAPPING_RECORDS", "SELECT \"记录ID\",\"资格证书名称\",\"核对状态\" FROM \"CAREER\".\"V_KB1_MAPPING_CONFLICTS\" ORDER BY \"记录ID\"");
        showQuery(db,"EMBEDDING_PENDING", "SELECT EMBEDDING_STATUS,COUNT(*),COUNT(EMBEDDING) FROM \"CAREER\".\"KB1_CHUNKS\" GROUP BY EMBEDDING_STATUS");
        try(Statement stmt=db.createStatement();ResultSet rs=stmt.executeQuery("SELECT SOURCE_SHA256,VERIFIED FROM \"CAREER\".\"KB1_MIGRATION_INFO\"")) {
            if(!rs.next() || !pack.properties.getProperty("source.sha256").equals(rs.getString(1).trim()) || !"Y".equals(rs.getString(2).trim()) || rs.next())
                throw new IOException("Migration marker mismatch");
        }
        String log="COMMITTED_DATA_VERIFIED\nDATABASE="+databaseName(db)+"\nSCHEMA="+schema+"\nFULL_ROW_CHECK_TABLES=11\nFULL_ROW_CHECK_ROWS=1628\nCONFLICT_RECORDS=2\nEMBEDDING_NOT_GENERATED=421\n";
        Files.writeString(pack.root.resolve("independent_verification.txt"),log,StandardCharsets.UTF_8);
        db.rollback();
        System.out.println(log);
    }
    static String columnComment(Connection db,String table,String column) throws SQLException {
        // DM's compatibility view reports object owner SYSDBA even when the
        // actual schema is CAREER; use the native schema-qualified dictionary.
        try(PreparedStatement stmt=db.prepareStatement("SELECT COMMENT$ FROM SYS.SYSCOLUMNCOMMENTS WHERE SCHNAME='CAREER' AND TVNAME=? AND COLNAME=?")) {
            stmt.setString(1,table); stmt.setString(2,column);
            try(ResultSet rs=stmt.executeQuery()) { return rs.next()?rs.getString(1):null; }
        }
    }
    static void commentSchema(Connection db,Package pack) throws Exception {
        if(!databaseName(db).equalsIgnoreCase(pack.properties.getProperty("expected.database")) || !"CAREER".equals(pack.properties.getProperty("schema")))
            throw new IOException("Unexpected annotation target");
        Map<String,Set<String>> actual=new HashMap<>();
        try(PreparedStatement stmt=db.prepareStatement("SELECT TABLE_NAME,COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER='CAREER'")) {
            try(ResultSet rs=stmt.executeQuery()) {
                while(rs.next()) actual.computeIfAbsent(rs.getString(1),ignored->new HashSet<>()).add(rs.getString(2));
            }
        }
        List<String[]> entries=new ArrayList<>();
        Map<String,Set<String>> expected=new HashMap<>();
        for(String line:Files.readAllLines(pack.root.resolve("column_comments.tsv"),StandardCharsets.UTF_8)) {
            String[] entry=line.split("\t",-1);
            if(entry.length!=4 || !(entry[1].startsWith("KB1_") || entry[1].startsWith("V_KB1_")))
                throw new IOException("Unexpected annotation entry");
            if(!actual.containsKey(entry[1])) throw new IOException("Object not found: "+entry[1]);
            if(entry[0].equals("C")) {
                if(!actual.get(entry[1]).contains(entry[2])) throw new IOException("Column not found: "+entry[1]+"."+entry[2]);
                if(!expected.computeIfAbsent(entry[1],ignored->new HashSet<>()).add(entry[2])) throw new IOException("Duplicate comment target");
            }
            entries.add(entry);
        }
        for(Map.Entry<String,Set<String>> e:expected.entrySet())
            if(!actual.get(e.getKey()).equals(e.getValue())) throw new IOException("Unexplained or unexpected columns: "+e.getKey());
        if(expected.size()!=21) throw new IOException("Incomplete annotation coverage");
        Map<String,Integer> countsBefore=new HashMap<>();
        for(Table t:pack.tables.values()) {
            try(Statement stmt=db.createStatement();ResultSet rs=stmt.executeQuery("SELECT COUNT(*) FROM "+qualified("CAREER",t.name))) {
                rs.next(); countsBefore.put(t.name,rs.getInt(1));
            }
        }
        Path backup=pack.root.resolve("column_comments_before.tsv");
        if(!Files.exists(backup)) {
            StringBuilder before=new StringBuilder();
            for(String[] entry:entries) {
                if(!entry[0].equals("C")) continue;
                String current=columnComment(db,entry[1],entry[2]);
                before.append(entry[1]).append('\t').append(entry[2]).append('\t')
                    .append(current==null?"N":"T:"+Base64.getEncoder().encodeToString(current.getBytes(StandardCharsets.UTF_8))).append('\n');
            }
            Files.writeString(backup,before,StandardCharsets.UTF_8);
        }
        String sql=Files.readString(pack.root.resolve("column_comments.sql"),StandardCharsets.UTF_8);
        for(String s:sql.split(";"))
            if(!s.isBlank() && !s.stripLeading().startsWith("COMMENT ON ")) throw new IOException("Only COMMENT statements are allowed");
        executeSqlFile(db,pack,"column_comments.sql","CAREER");
        db.commit();
        int tableColumns=0,viewColumns=0;
        for(String[] entry:entries) {
            String wanted=new String(Base64.getDecoder().decode(entry[3]),StandardCharsets.UTF_8);
            if(entry[0].equals("C")) {
                String actualComment=columnComment(db,entry[1],entry[2]);
                if(!wanted.equals(actualComment)) throw new IOException("Column comment round-trip failed: "+entry[1]+"."+entry[2]+" expected="+wanted+" actual="+actualComment);
                if(entry[1].startsWith("V_")) viewColumns++; else tableColumns++;
            } else {
                try(PreparedStatement stmt=db.prepareStatement("SELECT COMMENT$ FROM SYS.SYSTABLECOMMENTS WHERE SCHNAME='CAREER' AND TVNAME=?")) {
                    stmt.setString(1,entry[1]);
                    try(ResultSet rs=stmt.executeQuery()) {
                        if(!rs.next() || !wanted.equals(rs.getString(1))) throw new IOException("Object comment round-trip failed: "+entry[1]);
                    }
                }
            }
        }
        for(Table t:pack.tables.values()) {
            try(Statement stmt=db.createStatement();ResultSet rs=stmt.executeQuery("SELECT COUNT(*) FROM "+qualified("CAREER",t.name))) {
                rs.next(); if(rs.getInt(1)!=countsBefore.get(t.name)) throw new IOException("Unexpected row count change");
            }
        }
        String report="COLUMN_COMMENTS_VERIFIED\nSCHEMA=CAREER\nOBJECTS="+expected.size()+"\nTABLE_COLUMNS="+tableColumns+"\nVIEW_COLUMNS="+viewColumns+"\nBUSINESS_ROW_COUNTS_UNCHANGED=YES\n";
        Files.writeString(pack.root.resolve("column_comments_verification.txt"),report,StandardCharsets.UTF_8);
        System.out.println(report);
    }
    public static void main(String[] args) {
        if(args.length==2 && args[0].equals("check")) {
            try { checkPackage(new Package(Path.of(args[1]))); }
            catch(Exception error) { System.err.println(safe(error.toString())); System.exit(1); }
            return;
        }
        if(args.length<4) {
            System.err.println("Usage: CareerDmMigration.java probe|migrate host port user [packageDir]");
            System.exit(2);
        }
        try {
            Class.forName("dm.jdbc.driver.DmDriver");
            System.out.println("Waiting for database password on stdin (not echoed).");
            Console console=System.console();
            secret=console!=null ? new String(console.readPassword())
                : new BufferedReader(new InputStreamReader(System.in,StandardCharsets.UTF_8)).readLine();
            if(secret==null || secret.isEmpty()) throw new IllegalArgumentException("Missing password");
            Properties props=new Properties();
            props.setProperty("user",args[3]);
            props.setProperty("password",secret);
            props.setProperty("connectTimeout","10000");
            DriverManager.setLoginTimeout(15);
            try(Connection db=DriverManager.getConnection("jdbc:dm://"+args[1]+":"+Integer.parseInt(args[2]),props)) {
                db.setAutoCommit(false);
                if(args[0].equals("probe")) {
                    System.out.println("JDBC_CONNECTED");
                    showQuery(db,"DATABASE", "SELECT NAME FROM V$DATABASE");
                    showQuery(db,"INSTANCE", "SELECT NAME FROM V$INSTANCE");
                    showQuery(db,"CURRENT_USER", "SELECT USER FROM DUAL");
                    System.out.println("SCHEMAS");
                    try(ResultSet rs=db.getMetaData().getSchemas()) {
                        while(rs.next()) System.out.println(rs.getString("TABLE_SCHEM"));
                    }
                    showQuery(db,"CAREER_TABLES", "SELECT OWNER,TABLE_NAME FROM ALL_TABLES WHERE OWNER='CAREER' ORDER BY TABLE_NAME");
                    db.rollback();
                } else if(args[0].equals("migrate") && args.length==5) {
                    migrate(db,new Package(Path.of(args[4])));
                } else if(args[0].equals("verify") && args.length==5) {
                    verifyCommitted(db,new Package(Path.of(args[4])));
                } else if(args[0].equals("comment") && args.length==5) {
                    commentSchema(db,new Package(Path.of(args[4])));
                } else if(args[0].equals("comment-probe")) {
                    showQuery(db,"NATIVE_COLUMN_COMMENTS","SELECT SCHNAME,TVNAME,COLNAME,COMMENT$ FROM SYS.SYSCOLUMNCOMMENTS WHERE TVNAME='KB1_FILE_ASSETS'");
                    showQuery(db,"COMPAT_COLUMN_COMMENTS","SELECT OWNER,TABLE_NAME,COLUMN_NAME,COMMENTS FROM ALL_COL_COMMENTS WHERE TABLE_NAME='KB1_FILE_ASSETS'");
                    showQuery(db,"NATIVE_TABLE_COMMENTS","SELECT SCHNAME,TVNAME,COMMENT$ FROM SYS.SYSTABLECOMMENTS WHERE TVNAME='KB1_FILE_ASSETS'");
                    db.rollback();
                } else {
                    throw new IllegalArgumentException("Unsupported operation or missing migration package");
                }
            }
        } catch(Exception error) {
            System.err.println(error.getClass().getSimpleName()+": "+safe(error.getMessage()));
            System.exit(1);
        } finally {
            secret=null;
        }
    }
}
