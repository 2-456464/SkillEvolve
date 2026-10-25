"""基于业界真实检视数据构造评测集。

数据来源：Gerrit 真实人工检视数据集（Tufano et al. 学习格式，
HF 镜像 xin1997/codereview-java_all_only_input，Apache Calcite / Mesos /
Besu(Pantheon) / Liquibase / Sentry 等开源项目的 diff + 人工 review 意见）。

构造方式：
  - 每条案例取一条真实「diff hunk + 人工检视意见」，重建为自包含可编译的
    完整 Java 类（仅 JDK 依赖）；检视对象 = 变更后完整源码（含 reviewer
    指出的缺陷），标准答案 GT = 真实人工意见的中文结构化结果。
  - 干净案例取真实意见为「纯文档/措辞建议」的样本，变更后代码无缺陷，GT 为空。
  - 生成后立即用 javac 逐条编译验证，编译不通过的案例直接报错退出。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent / "eval_set.jsonl"

CASES: list[dict] = [
    # ------------------------------------------------------------- 缺陷 1：未判空
    {
        "id": "case-101",
        "origin": "gerrit/tufano-dataset#codereview_java_data_167",
        "language": "java",
        "file_path": "src/main/java/com/example/logs/LogPathBuilder.java",
        "change_summary": "日志路径构建器新增 getLogFileParentDir 方法：按前缀与主题逐级拼接日志父目录",
        "source_code": '''package com.example.logs;

import java.util.ArrayList;
import java.util.List;

/** 按前缀/主题构建日志文件的父目录路径。 */
public class LogPathBuilder {

    private final String mPrefix;
    private final String mTopic;

    public LogPathBuilder(String prefix, String topic) {
        this.mPrefix = prefix;
        this.mTopic = topic;
    }

    public String getLogFileParentDir() {
        List<String> elements = new ArrayList<String>();
        if (mPrefix.length() > 0) {
            elements.add(mPrefix);
        }
        if (mTopic.length() > 0) {
            elements.add(mTopic);
        }
        return String.join("/", elements);
    }
}
''',
        "diff": '''--- a/src/main/java/com/example/logs/LogPathBuilder.java
+++ b/src/main/java/com/example/logs/LogPathBuilder.java
@@ -12,4 +12,12 @@ public class LogPathBuilder {
     }

+    public String getLogFileParentDir() {
+        List<String> elements = new ArrayList<String>();
+        if (mPrefix.length() > 0) {
+            elements.add(mPrefix);
+        }
+        if (mTopic.length() > 0) {
+            elements.add(mTopic);
+        }
+        return String.join("/", elements);
+    }
 }
''',
        "ground_truth": [
            {
                "id": "GT-1",
                "category": "robustness",
                "severity": "high",
                "title": "getLogFileParentDir 对 mPrefix/mTopic 未判空直接调用 length()",
                "detail": "构造入参 prefix/topic 允许调用方传 null（无任何约束），方法内直接 mPrefix.length() 会抛 NullPointerException。真实检视意见要求改为 if (mPrefix != null && mPrefix.length() > 0)，两个 if 均需补判空，且 if 关键字后应留空格。",
                "line_hint": "getLogFileParentDir() 中的 if (mPrefix.length() > 0)",
            },
        ],
        "gt_count": 1,
        "is_clean_case": False,
    },
    # ------------------------------------------------- 缺陷 2：异常 cause 未解包
    {
        "id": "case-102",
        "origin": "gerrit/tufano-dataset#codereview_java_data_208",
        "language": "java",
        "file_path": "src/main/java/com/example/mesos/TaskFileFetcher.java",
        "change_summary": "任务文件下载器新增 fetch 异常分类：连接类异常包装为 TaskNotFoundException，其余直接抛出",
        "source_code": '''package com.example.mesos;

import java.net.ConnectException;
import java.io.IOException;
import java.util.concurrent.ExecutionException;

/** 从 Mesos slave 拉取任务文件。 */
public class TaskFileFetcher {

    /** 拉取失败（网络不可达等）时抛出。 */
    public static class TaskNotFoundException extends RuntimeException {
        public TaskNotFoundException(Throwable cause) {
            super(cause);
        }
    }

    public byte[] fetch(String filePath) {
        try {
            return readResponse(filePath);
        } catch (ExecutionException | ConnectException ce) {
            throw new TaskNotFoundException(ce);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    /** 模拟底层 HTTP 调用：连接被拒时以 ExecutionException 包装 ConnectException 抛出。 */
    private byte[] readResponse(String filePath) throws ExecutionException, IOException {
        try {
            throw new java.net.ConnectException("Connection refused to slave:5051");
        } catch (java.net.ConnectException direct) {
            throw new ExecutionException(direct);
        }
    }
}
''',
        "diff": '''--- a/src/main/java/com/example/mesos/TaskFileFetcher.java
+++ b/src/main/java/com/example/mesos/TaskFileFetcher.java
@@ -18,7 +18,7 @@ public class TaskFileFetcher {
     public byte[] fetch(String filePath) {
         try {
             return readResponse(filePath);
-        } catch (Exception e) {
-            throw new RuntimeException(e);
+        } catch (ExecutionException | ConnectException ce) {
+            throw new TaskNotFoundException(ce);
+        } catch (Exception e) {
+            throw new RuntimeException(e);
         }
     }
''',
        "ground_truth": [
            {
                "id": "GT-1",
                "category": "correctness",
                "severity": "high",
                "title": "catch 分支把 ExecutionException 与其内层 ConnectException 并列捕获，连接异常永远命中不了",
                "detail": "底层 ConnectException 通常作为 ExecutionException 的 cause 被包装抛出（多步异步调用场景），直接 catch ConnectException 无法匹配外层 ExecutionException。真实检视意见建议：捕获 ExecutionException 后检查 getCause() 是否为 ConnectException，是则抛 SlaveNotFound/TaskNotFound，否则按其他异常处理。",
                "line_hint": "catch (ExecutionException | ConnectException ce)",
            },
        ],
        "gt_count": 1,
        "is_clean_case": False,
    },
    # ------------------------------------------------------- 缺陷 3：魔法数字
    {
        "id": "case-103",
        "origin": "gerrit/tufano-dataset#codereview_java_data_991",
        "language": "java",
        "file_path": "src/main/java/com/example/chain/CliqueRecovery.java",
        "change_summary": "Clique 共识新增 recoverProposerAddress：从区块头与提议者印章恢复提议者地址",
        "source_code": '''package com.example.chain;

/** Clique PoA 提议者地址恢复。 */
public class CliqueRecovery {

    public static class BlockHeader {
        public long getNumber() {
            return 0L;
        }
    }

    public static class Address {
        public static final Address ZERO = new Address(new byte[20]);
        private final byte[] bytes;

        public Address(byte[] bytes) {
            this.bytes = bytes;
        }

        public static Address extract(byte[] seal) {
            byte[] out = new byte[20];
            System.arraycopy(seal, seal.length - 20, out, 0, 20);
            return new Address(out);
        }
    }

    public static Address recoverProposerAddress(final BlockHeader header, final byte[] proposerSeal) {
        if (header.getNumber() == 0) {
            return Address.ZERO;
        }
        if (proposerSeal == null || proposerSeal.length == 0) {
            throw new IllegalArgumentException("Clique extra data proposer seal is missing");
        }
        return Address.extract(proposerSeal);
    }
}
''',
        "diff": '''--- a/src/main/java/com/example/chain/CliqueRecovery.java
+++ b/src/main/java/com/example/chain/CliqueRecovery.java
@@ -27,6 +27,8 @@ public class CliqueRecovery {
     }

     public static Address recoverProposerAddress(
-        final BlockHeader header, final byte[] proposerSeal) {
-      return Address.extract(proposerSeal);
+        final BlockHeader header, final byte[] proposerSeal) {
+      if (header.getNumber() == 0) {
+        return Address.ZERO;
+      }
+      return Address.extract(proposerSeal);
     }
''',
        "ground_truth": [
            {
                "id": "GT-1",
                "category": "maintainability",
                "severity": "medium",
                "title": "硬编码创世块高度 0 判断创世块",
                "detail": "直接写 header.getNumber() == 0 判断创世块，将来支持可变创世块高度（非 0 起点）时必须逐处修改且极易遗漏。真实检视意见明确要求：不要硬编码 0，应使用 BlockHeader.GENESIS_BLOCK_NUMBER 这类命名常量。",
                "line_hint": "recoverProposerAddress() 中的 if (header.getNumber() == 0)",
            },
        ],
        "gt_count": 1,
        "is_clean_case": False,
    },
    # ----------------------------------------------------- 缺陷 4：单位常量用错
    {
        "id": "case-104",
        "origin": "gerrit/tufano-dataset#codereview_java_data_581",
        "language": "java",
        "file_path": "src/main/java/com/example/tdd/TddCalculator.java",
        "change_summary": "总量日(TDD)计算器新增目标值换算 targetTdd：按当前 Profile 的单位制换算目标值",
        "source_code": '''package com.example.tdd;

/** 血糖目标值换算（简化自开源闭环项目 AndroidAPS）。 */
class Units {
    static final String MGDL = "mg/dl";
    static final String MMOL = "mmol/L";
}

class Profile {
    private final String units;

    Profile(String units) {
        this.units = units;
    }

    String getUnits() {
        return units;
    }

    /** 将 mmol/L 数值换算为 mg/dl。 */
    static double toMgdl(double mmol, String unit) {
        return Units.MMOL.equals(unit) ? mmol * 18.0d : mmol;
    }
}

public class TddCalculator {

    public double targetTdd(Double prefTT, Profile currentProfile) {
        if (currentProfile == null) {
            return 0d;
        }
        double tt;
        if (Units.MMOL.equals(currentProfile.getUnits())) {
            tt = prefTT != null && prefTT > 0 ? Profile.toMgdl(prefTT, Units.MGDL) : 80d;
        } else {
            tt = prefTT != null && prefTT > 0 ? prefTT : 80d;
        }
        return tt;
    }
}
''',
        "diff": '''--- a/src/main/java/com/example/tdd/TddCalculator.java
+++ b/src/main/java/com/example/tdd/TddCalculator.java
@@ -32,6 +32,8 @@ public class TddCalculator {
         if (currentProfile == null) {
             return 0d;
         }
+        double tt;
+        if (Units.MMOL.equals(currentProfile.getUnits()))
+            tt = prefTT != null && prefTT > 0 ? Profile.toMgdl(prefTT, Units.MGDL) : 80d;
+        else
+            tt = prefTT != null && prefTT > 0 ? prefTT : 80d;
+        return tt;
     }
 }
''',
        "ground_truth": [
            {
                "id": "GT-1",
                "category": "correctness",
                "severity": "critical",
                "title": "MMOL 分支误用 MGDL 常量调用 toMgdl，单位换算方向错误",
                "detail": "进入该分支的前提是当前 Profile 单位为 MMOL，但换算调用写的是 Profile.toMgdl(prefTT, Constants.MGDL)——传成了 MGDL，toMgdl 对非 MMOL 单位原样返回，mmol 值未乘 18，目标值偏小 18 倍，直接影响胰岛素剂量计算。真实检视意见给出修正：tt = prefTT > 0 ? Profile.toMgdl(prefTT, Constants.MMOL) : 80d，并可据此合并 if/else。",
                "line_hint": "tt = prefTT != null && prefTT > 0 ? Profile.toMgdl(prefTT, Units.MGDL) : 80d",
            },
        ],
        "gt_count": 1,
        "is_clean_case": False,
    },
    # --------------------------------------------------- 缺陷 5：方言条件缺失
    {
        "id": "case-105",
        "origin": "gerrit/tufano-dataset#codereview_java_data_606",
        "language": "java",
        "file_path": "src/main/java/com/example/sql/ColumnParser.java",
        "change_summary": "SQL 方言解析器新增 CONVERT 类型转换语法的识别分支",
        "source_code": '''package com.example.sql;

/** 简化 SQL 解析器（简化自 Liquibase）。 */
public class ColumnParser {

    enum ModeEnum { MySQL, Oracle, Postgres }

    static class Database {
        ModeEnum getMode() {
            return ModeEnum.Postgres;
        }
    }

    private final Database database;
    private String token;

    public ColumnParser(Database database, String token) {
        this.database = database;
        this.token = token;
    }

    private boolean readIf(String keyword) {
        if (keyword.equalsIgnoreCase(token)) {
            token = "";
            return true;
        }
        return false;
    }

    public String parseConvert() {
        String columnName = "col";
        String newColumnName = "new_col";
        if (readIf("CAST")) {
            return "cast(" + columnName + " as " + newColumnName + ")";
        } else if (readIf("CONVERT")) {
            readIf("TO");
            readIf("CHARACTER");
            readIf("SET");
            return "convert(" + columnName + ", " + newColumnName + ")";
        }
        return columnName;
    }
}
''',
        "diff": '''--- a/src/main/java/com/example/sql/ColumnParser.java
+++ b/src/main/java/com/example/sql/ColumnParser.java
@@ -29,5 +29,7 @@ public class ColumnParser {
         if (readIf("CAST")) {
             return "cast(" + columnName + " as " + newColumnName + ")";
-        }
+        } else if (readIf("CONVERT")) {
+            readIf("TO");
+            readIf("CHARACTER");
+            readIf("SET");
+            return "convert(" + columnName + ", " + newColumnName + ")";
+        }
         return columnName;
     }
''',
        "ground_truth": [
            {
                "id": "GT-1",
                "category": "correctness",
                "severity": "high",
                "title": "CONVERT 分支未限定 MySQL 方言，其他数据库模式会被误解析",
                "detail": "CONVERT ... TO CHARACTER SET 是 MySQL 专有语法，但分支条件只有 readIf(\"CONVERT\")，Oracle/Postgres 模式下同样命中并生成 MySQL 风格表达式。真实检视意见要求补上 && database.getMode().getEnum() == ModeEnum.MySQL 限定，避免其它数据库测试意外走进该分支。",
                "line_hint": "parseConvert() 中的 } else if (readIf(\"CONVERT\")) {",
            },
        ],
        "gt_count": 1,
        "is_clean_case": False,
    },
    # ------------------------------------------------------- 缺陷 6：锁归属错误
    {
        "id": "case-106",
        "origin": "gerrit/tufano-dataset#codereview_java_data_568",
        "language": "java",
        "file_path": "src/main/java/com/example/upload/UploaderService.java",
        "change_summary": "上传服务基类新增 checkFileOpenLock 成员用于文件打开互斥，并在构造器中初始化",
        "source_code": '''package com.example.upload;

import java.nio.file.Path;
import java.util.concurrent.locks.ReentrantLock;

/** 抽象上传服务（简化自 Sentry 上传驱动）。 */
public abstract class UploaderService {

    protected final ReentrantLock checkFileOpenLock;

    protected UploaderService(UploaderConfiguration configuration, ExceptionNotifier exceptionNotifier) {
        this.checkFileOpenLock = new ReentrantLock();
    }

    /** 逐个上传文件，由子类实现。 */
    protected abstract void uploadSingle(int sequence, Path file) throws Exception;

    static class UploaderConfiguration {
    }

    static class ExceptionNotifier {
    }
}
''',
        "diff": '''--- a/src/main/java/com/example/upload/UploaderService.java
+++ b/src/main/java/com/example/upload/UploaderService.java
@@ -8,6 +8,8 @@ public abstract class UploaderService {
     protected UploaderService(UploaderConfiguration configuration,
                               ExceptionNotifier exceptionNotifier) {
-        // no shared lock
+        this.checkFileOpenLock = new ReentrantLock();
     }

     protected abstract void uploadSingle(int sequence, Path file) throws Exception;
''',
        "ground_truth": [
            {
                "id": "GT-1",
                "category": "robustness",
                "severity": "high",
                "title": "锁在构造器中 new，多实例场景下互斥失效",
                "detail": "checkFileOpenLock 在每个实例的构造器里 new ReentrantLock()。该类是抽象基类，真正的单例是上层的 uploader driver 而非本服务——若创建多个服务实例，每个实例各持一把锁，checkFileOpenLock 完全失去互斥意义。真实检视意见明确质疑：这个类总是单例吗？锁应该由单例方持有并注入。",
                "line_hint": "构造器中的 this.checkFileOpenLock = new ReentrantLock()",
            },
        ],
        "gt_count": 1,
        "is_clean_case": False,
    },
    # ------------------------------------------------ 缺陷 7：网络响应判空不全
    {
        "id": "case-107",
        "origin": "gerrit/tufano-dataset#codereview_java_data_311",
        "language": "java",
        "file_path": "src/main/java/com/example/http/FeedbackClient.java",
        "change_summary": "反馈上报客户端新增 fetchFeedback：请求远端接口并解析 FeedbackResponse，失败时返回 null",
        "source_code": '''package com.example.http;

import java.net.SocketTimeoutException;

/** 反馈上报客户端（简化 OkHttp 用法为内置 HttpStack）。 */
public class FeedbackClient {

    public static class Response {
        private final String body;
        private final boolean successful;

        Response(String body, boolean successful) {
            this.body = body;
            this.successful = successful;
        }

        public String body() {
            return body;
        }

        public boolean isSuccessful() {
            return successful;
        }
    }

    public static class FeedbackResponse {
        public String message;
    }

    /**
     * 拉取远端反馈配置；网络失败/无数据时返回 null。
     */
    public FeedbackResponse fetchFeedback(String url) {
        Response response;
        try {
            response = executeRequest(url);
        } catch (java.io.IOException e) {
            return null;
        }
        if (response.body() != null && response.isSuccessful()) {
            FeedbackResponse parsed = new FeedbackResponse();
            parsed.message = response.body();
            return parsed;
        }
        return null;
    }

    /** 模拟底层调用：可能抛 SocketTimeoutException（超时）。 */
    private Response executeRequest(String url) throws SocketTimeoutException {
        throw new SocketTimeoutException("timeout");
    }
}
''',
        "diff": '''--- a/src/main/java/com/example/http/FeedbackClient.java
+++ b/src/main/java/com/example/http/FeedbackClient.java
@@ -33,6 +33,8 @@ public class FeedbackClient {
     public FeedbackResponse fetchFeedback(String url) {
         Response response;
         try {
             response = executeRequest(url);
-        } catch (java.io.IOException e) {
+        } catch (java.io.IOException e) {
             return null;
         }
+        if (response.body() != null && response.isSuccessful()) {
+            FeedbackResponse parsed = new FeedbackResponse();
+            parsed.message = response.body();
+            return parsed;
+        }
         return null;
     }
''',
        "ground_truth": [
            {
                "id": "GT-1",
                "category": "robustness",
                "severity": "medium",
                "title": "executeRequest 可能抛出 SocketTimeoutException，未按『失败返回 null』契约处理",
                "detail": "executeRequest 声明抛出 SocketTimeoutException，而 fetchFeedback 只捕获 IOException 的部分场景后返回 null——超时异常会直接穿透到调用方，与 javadoc『失败返回 null』的契约不一致。真实检视意见指出：线上正是这里抛出的 SocketTimeoutException 引发崩溃，应捕获超时并返回 null。",
                "line_hint": "fetchFeedback() 中的 try { response = executeRequest(url); }",
            },
            {
                "id": "GT-2",
                "category": "robustness",
                "severity": "medium",
                "title": "response 本身未判空，仅判了 body()",
                "detail": "条件 response.body() != null && response.isSuccessful() 假定 executeRequest 一定返回非 null。方法既然以 null 表示失败，底层也可能返回 null 响应，应先判 response != null 再取 body()。真实检视意见同样要求把判空下沉到 response 层。",
                "line_hint": "if (response.body() != null && response.isSuccessful())",
            },
        ],
        "gt_count": 2,
        "is_clean_case": False,
    },
    # ------------------------------------------------ 缺陷 8：魔法字节/常量提取
    {
        "id": "case-108",
        "origin": "gerrit/tufano-dataset#codereview_java_data_313",
        "language": "java",
        "file_path": "src/main/java/com/example/consensus/Vote.java",
        "change_summary": "Clique 投票信封新增 Vote 类型与投票值的双向映射表",
        "source_code": '''package com.example.consensus;

import java.util.HashMap;
import java.util.Map;

/** Clique 投票（简化自 Pantheon/Besu）。 */
public class Vote {

    public enum VoteType {
        ADD, DROP
    }

    public static final Map<VoteType, Byte> VOTE_TO_VALUE = new HashMap<VoteType, Byte>();

    static {
        VOTE_TO_VALUE.put(VoteType.ADD, (byte) 0xFF);
        VOTE_TO_VALUE.put(VoteType.DROP, (byte) 0x0);
    }

    private final String recipient;
    private final VoteType voteType;

    public Vote(String recipient, VoteType voteType) {
        this.recipient = recipient;
        this.voteType = voteType;
    }

    public byte encodedValue() {
        return VOTE_TO_VALUE.get(voteType);
    }
}
''',
        "diff": '''--- a/src/main/java/com/example/consensus/Vote.java
+++ b/src/main/java/com/example/consensus/Vote.java
@@ -12,5 +12,7 @@ public class Vote {
     public static final Map<VoteType, Byte> VOTE_TO_VALUE = new HashMap<VoteType, Byte>();

     static {
+        VOTE_TO_VALUE.put(VoteType.ADD, (byte) 0xFF);
+        VOTE_TO_VALUE.put(VoteType.DROP, (byte) 0x0);
     }
''',
        "ground_truth": [
            {
                "id": "GT-1",
                "category": "maintainability",
                "severity": "medium",
                "title": "投票值使用内联魔法字节，应提取为命名常量",
                "detail": "(byte) 0xFF / (byte) 0x0 直接内联在静态映射构造里，语义不明、易写错且无法在别处复用。真实检视意见建议：ADD/DROP 对应的字节值应提取为 public 常量 ADD_NONCE / DROP_NONCE，映射表引用常量而不是裸值。",
                "line_hint": "VOTE_TO_VALUE.put(VoteType.ADD, (byte) 0xFF)",
            },
        ],
        "gt_count": 1,
        "is_clean_case": False,
    },
    # ------------------------------------------------ 干净案例 1：文档修正
    {
        "id": "case-109",
        "origin": "gerrit/tufano-dataset#codereview_java_data_35",
        "language": "java",
        "file_path": "src/main/java/com/example/config/XmlJetConfigBuilder.java",
        "change_summary": "配置构建器新增 setProperties 与变量解析 resolve：仅修正 javadoc 对 null 参数行为的描述",
        "source_code": '''package com.example.config;

import java.util.Properties;

/** XML 配置构建器（简化自 JspWiki / Jetty 配置装载）。 */
public class XmlJetConfigBuilder {

    private Properties properties;

    /**
     * Sets properties to be used in variable resolution.
     *
     * If null properties supplied, System properties will be used.
     *
     * @param properties the properties to be used to resolve ${variable}
     *                   occurrences in the XML file
     * @return the XmlJetConfigBuilder
     */
    public XmlJetConfigBuilder setProperties(Properties properties) {
        this.properties = properties;
        return this;
    }

    /** 解析 ${variable} 占位符；未设置 properties 时回退到系统属性。 */
    public String resolve(String template) {
        Properties effective = properties != null ? properties : System.getProperties();
        String out = template;
        for (String name : effective.stringPropertyNames()) {
            out = out.replace("${" + name + "}", effective.getProperty(name));
        }
        return out;
    }
}
''',
        "diff": '''--- a/src/main/java/com/example/config/XmlJetConfigBuilder.java
+++ b/src/main/java/com/example/config/XmlJetConfigBuilder.java
@@ -10,9 +10,10 @@ public class XmlJetConfigBuilder {
     /**
-     * Sets the used properties. Can be null if no properties should be used.
+     * Sets properties to be used in variable resolution.
      *
-     * Properties are used to resolve ${variable} occurrences in the XML file.
+     * If null properties supplied, System properties will be used.
      *
+     * @param properties the properties to be used to resolve ${variable}
+     *                   occurrences in the XML file
      * @return the XmlJetConfigBuilder
      */
     public XmlJetConfigBuilder setProperties(Properties properties) {
''',
        "ground_truth": [],
        "gt_count": 0,
        "is_clean_case": True,
    },
    # ------------------------------------------------ 干净案例 2：异常消息措辞
    {
        "id": "case-110",
        "origin": "gerrit/tufano-dataset#codereview_java_data_407",
        "language": "java",
        "file_path": "src/main/java/com/example/id/AbstractId.java",
        "change_summary": "标识值对象基类新增构造校验：仅改进 IllegalArgumentException 的消息措辞以引用参数名",
        "source_code": '''package com.example.id;

import java.util.Objects;

/** 标识值对象基类（简化自 Tessera）。 */
public abstract class AbstractId {

    private final String canonical;

    protected AbstractId(final String canonical) {
        if (canonical == null || canonical.trim().isEmpty()) {
            throw new IllegalArgumentException("canonical id provided can't be empty or null.");
        }
        this.canonical = canonical;
    }

    public final String canonicalValue() {
        return Objects.requireNonNull(canonical);
    }

    @Override
    public String toString() {
        return getClass().getSimpleName() + "{" + canonical + "}";
    }
}
''',
        "diff": '''--- a/src/main/java/com/example/id/AbstractId.java
+++ b/src/main/java/com/example/id/AbstractId.java
@@ -12,7 +12,7 @@ public abstract class AbstractId {
     protected AbstractId(final String canonical) {
         if (canonical == null || canonical.trim().isEmpty())
-            throw new IllegalArgumentException("Id string provided can't be empty or null.");
+            throw new IllegalArgumentException("canonical id provided can't be empty or null.");
         this.canonical = canonical;
     }
''',
        "ground_truth": [],
        "gt_count": 0,
        "is_clean_case": True,
    },
]


def compile_check(case: dict) -> tuple[bool, str]:
    """javac 编译 source_code，返回 (ok, 错误输出)。文件名取 public 类名。"""
    m = re.search(r"public\s+(?:abstract\s+|final\s+)?class\s+(\w+)", case["source_code"])
    cls = m.group(1) if m else "Test"
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"{cls}.java"
        src.write_text(case["source_code"], encoding="utf-8")
        proc = subprocess.run(
            ["javac", "-encoding", "utf-8", "-d", td, str(src)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout)[-1500:]
        return True, ""


def main() -> int:
    import re

    all_ok = True
    for case in CASES:
        # 基本校验
        assert case["gt_count"] == len(case["ground_truth"]), f'{case["id"]} gt_count 不一致'
        assert case["is_clean_case"] == (not case["ground_truth"]), f'{case["id"]} clean 标志不一致'
        # diff 必须引用 file_path
        fname = Path(case["file_path"]).name
        if fname not in case["diff"]:
            print(f'[FAIL] {case["id"]} diff 未引用文件名 {fname}')
            all_ok = False
        # javac 编译验证
        ok, err = compile_check(case)
        n_lines = len(case["source_code"].splitlines())
        print(f'  [{ "OK" if ok else "FAIL"}] {case["id"]} {fname} ({n_lines} 行, GT={case["gt_count"]}, origin={case["origin"].split("#")[1]})')
        if not ok:
            print("    " + err.replace("\n", "\n    ")[:1200])
            all_ok = False
    if not all_ok:
        print("存在编译失败案例，已中止写出")
        return 1

    with OUT.open("w", encoding="utf-8", newline="\n") as f:
        for case in CASES:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
    gt_total = sum(c["gt_count"] for c in CASES)
    print(f"写出 {len(CASES)} 条案例（GT 共 {gt_total} 条）→ {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
