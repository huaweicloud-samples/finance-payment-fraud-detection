package sample.paymentfraud.collector;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.text.DecimalFormat;
import java.util.Calendar;
import java.util.TimeZone;
import java.util.logging.Level;
import java.util.logging.Logger;

import org.json.JSONObject;

import com.obs.services.ObsClient;
import com.obs.services.ObsConfiguration;
import com.obs.services.exception.ObsException;
import com.obs.services.model.GetObjectMetadataRequest;
import com.obs.services.model.GetObjectRequest;
import com.obs.services.model.ObsObject;

/**
 * 对象存储服务封装（OBS）。
 * 支持两种认证模式：
 *  1. 环境变量注入 OBS_ENDPOINT / OBS_ACCESS_KEY / OBS_SECRET_KEY（推荐，凭据走 k8s Secret）
 *  2. 未注入时使用默认端点（需节点具备 OBS 访问委托）
 */
public class StorageService {

    private final static Logger LOGGER = Logger.getLogger(StorageService.class.getName());

    CollectorConfig collectorConfig = new CollectorConfig();

    private ObsClient obs = null;
    private String endpoint = null;

    public StorageService() throws Exception {
        String obsEndpoint = getCollectorConfig().getConfig(CollectorConfig.Configs.OBS_ENDPOINT);
        String accessKey = getCollectorConfig().getConfig(CollectorConfig.Configs.OBS_ACCESS_KEY);
        String secretKey = getCollectorConfig().getConfig(CollectorConfig.Configs.OBS_SECRET_KEY);

        if (obsEndpoint == null || obsEndpoint.trim().isEmpty()) {
            obsEndpoint = "https://obs.cn-north-4.myhuaweicloud.com";
        }
        this.endpoint = obsEndpoint;
        LOGGER.info("OBS endpoint is - " + obsEndpoint);

        ObsConfiguration config = new ObsConfiguration();
        config.setEndPoint(obsEndpoint);
        config.setPathStyle(false);

        if (accessKey != null && !accessKey.trim().isEmpty()
                && secretKey != null && !secretKey.trim().isEmpty()) {
            obs = new ObsClient(accessKey.trim(), secretKey.trim(), config);
            LOGGER.info("Initialized OBS with AK/SK from environment");
        } else {
            // 依赖节点 IAM 委托（op_svc_cce agency → OBS OperateAccess）
            obs = new ObsClient(config);
            LOGGER.info("Initialized OBS with default credential chain");
        }
    }

    public String storeFile(String data) {
        Calendar calendar = Calendar.getInstance(TimeZone.getTimeZone("UTC"));
        DecimalFormat mFormat = new DecimalFormat("00");
        String date = mFormat.format(calendar.get(Calendar.DATE));
        String month = mFormat.format(calendar.get(Calendar.MONTH) + 1);
        String year = mFormat.format(calendar.get(Calendar.YEAR));
        String hour = mFormat.format(calendar.get(Calendar.HOUR_OF_DAY));
        String minute = mFormat.format(calendar.get(Calendar.MINUTE));
        String second = mFormat.format(calendar.get(Calendar.SECOND));

        String objectKey = new StringBuilder()
                .append(getCollectorConfig().getConfig(CollectorConfig.Configs.COLLECTOR_FOLDER))
                .append("/")
                .append(year).append("/")
                .append(month).append("/")
                .append(date).append("/")
                .append(hour).append("/")
                .append(minute).append("/")
                .append(second).append("/").toString();

        String location = store(data, objectKey + "file.csv");
        return location;
    }

    private byte[] readStateFileFromStorage(String stateFileName) throws IOException {
        String bucket = getCollectorConfig().getConfig(CollectorConfig.Configs.OBS_BUCKET);
        try {
            // doesObjectExist: 对象不存在时返回 false（无异常）
            GetObjectMetadataRequest metadataRequest = new GetObjectMetadataRequest(bucket, stateFileName);
            if (!obs.doesObjectExist(metadataRequest)) {
                LOGGER.info("State file " + stateFileName + " does not exist");
                return null;
            }
            GetObjectRequest request = new GetObjectRequest(bucket, stateFileName);
            ObsObject object = obs.getObject(request);
            try (InputStream in = object.getObjectContent()) {
                return in.readAllBytes();
            }
        } catch (ObsException e) {
            if (e.getResponseCode() == 404) {
                LOGGER.info("State file " + stateFileName + " does not exist");
                return null;
            }
            LOGGER.log(Level.SEVERE, "Error occurred while retrieving state file", e);
            return null;
        } catch (Exception e) {
            LOGGER.log(Level.SEVERE, "Error occurred while retrieving state file", e);
            return null;
        }
    }

    public JSONObject getCollectorState() throws IOException {
        byte[] responseBytes = readStateFileFromStorage(
                getCollectorConfig().getConfig(CollectorConfig.Configs.COLLECTOR_STATE));
        if (responseBytes == null) return null;

        String fileContent = new String(responseBytes);
        return new JSONObject(fileContent);
    }

    public String store(String data, String objectName) {
        if (data == null || data.isBlank()) {
            LOGGER.info("No data to store for name " + objectName);
            return null;
        }

        String bucket = getCollectorConfig().getConfig(CollectorConfig.Configs.OBS_BUCKET);
        try {
            obs.putObject(bucket, objectName, new ByteArrayInputStream(data.getBytes()));
            return bucket + "/" + objectName;
        } catch (ObsException e) {
            LOGGER.log(Level.SEVERE, "Failed to store object " + objectName, e);
            throw e;
        }
    }

    private CollectorConfig getCollectorConfig() {
        return collectorConfig;
    }

    public String getEndpoint() {
        return endpoint;
    }
}
