package sample.paymentfraud.collector;

import java.util.logging.Logger;

public class CollectorConfig {

    public enum Configs {
        FILE_DATA_EXTRACT_FOLDER_IN_OBS,
        FILE_LINE_NUMBER_TO_READ_FROM,
        FILES_LOCATION_IN_OBS,
        MAX_LINES_TO_READ,
        PAYMENT_DATA_FILE,
        REGION,
        OBS_BUCKET,
        COLLECTOR_STATE,
        COLLECTOR_FOLDER,
        TEST_MODE,
        OBS_ENDPOINT,
        OBS_ACCESS_KEY,
        OBS_SECRET_KEY
    }

    public static final String COLLECTOR_STATE_START_LINE_NUMBER = "startLineNumber";

    private final static Logger LOGGER = Logger.getLogger(CollectorConfig.class.getName());

    public CollectorConfig() {
        LOGGER.info("FILE_DATA_EXTRACT_FOLDER_IN_OBS - " + System.getenv("FILE_DATA_EXTRACT_FOLDER_IN_OBS"));
        LOGGER.info("FILE_LINE_NUMBER_TO_READ_FROM - " + System.getenv("FILE_LINE_NUMBER_TO_READ_FROM"));
        LOGGER.info("FILES_LOCATION_IN_OBS - " + System.getenv("FILES_LOCATION_IN_OBS"));
        LOGGER.info("MAX_LINES_TO_READ - " + System.getenv("MAX_LINES_TO_READ"));
        LOGGER.info("PAYMENT_DATA_FILE - " + System.getenv("PAYMENT_DATA_FILE"));
        LOGGER.info("REGION - " + System.getenv("REGION"));
        LOGGER.info("OBS_BUCKET - " + System.getenv("OBS_BUCKET"));
        LOGGER.info("COLLECTOR_STATE - " + System.getenv("COLLECTOR_STATE"));
        LOGGER.info("COLLECTOR_FOLDER - " + System.getenv("COLLECTOR_FOLDER"));
        LOGGER.info("TEST_MODE - " + System.getenv("TEST_MODE"));
        LOGGER.info("OBS_ENDPOINT - " + System.getenv("OBS_ENDPOINT"));
        LOGGER.info("OBS_ACCESS_KEY - " + mask(System.getenv("OBS_ACCESS_KEY")));
        LOGGER.info("OBS_SECRET_KEY - " + mask(System.getenv("OBS_SECRET_KEY")));
    }

    private static String mask(String value) {
        if (value == null || value.isEmpty()) return value;
        return value.substring(0, Math.min(3, value.length())) + "***";
    }

    public String getConfig(CollectorConfig.Configs key) {
        /* if(key.equals(Configs.FILE_DATA_EXTRACT_FOLDER_IN_OBS)) return "file_extract";
        else if(key.equals(Configs.FILE_LINE_NUMBER_TO_READ_FROM)) return "50001";
        else if (key.equals(Configs.FILES_LOCATION_IN_OBS)) return "raw_payment_request_files";
        else if (key.equals(Configs.MAX_LINES_TO_READ)) return "100000";
        else if (key.equals(Configs.PAYMENT_DATA_FILE)) return "transaction_data_100K_full.csv";
        else if (key.equals(Configs.OBS_BUCKET)) return "spf-runtime-cn-north-4";
        else if (key.equals(Configs.REGION)) return "cn-north-4";
        else if (key.equals(Configs.COLLECTOR_STATE)) return "collector-state.json";
        else if (key.equals(Configs.TEST_MODE)) return "true";
        else if (key.equals(Configs.COLLECTOR_FOLDER)) return "data-collector";
        else if (key.equals(Configs.OBS_ENDPOINT)) return "";
        else if (key.equals(Configs.OBS_ACCESS_KEY)) return "";
        else if (key.equals(Configs.OBS_SECRET_KEY)) return "";
        return null; */
        return System.getenv(key.toString());
    }

    /* public static void main(String[] args) {
        ConfigMapCollectorConfig config = new ConfigMapCollectorConfig();
        
        LocalDateTime currentDateTime = LocalDateTime.now();

        // Print the timestamp
        System.out.println("Timestamp: " + currentDateTime);
        //config.updateParameter("/fraud-detection-app/collector/config/scan_timestamp",currentDateTime.toString());
    } */

}
