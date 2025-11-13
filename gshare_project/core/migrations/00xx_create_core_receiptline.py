from django.db import migrations

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS `core_receiptline` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `receipt_id` bigint NOT NULL,
  `name` varchar(256) NOT NULL,
  `quantity` double NOT NULL DEFAULT 1,
  `unit_price` double DEFAULT NULL,
  `total_price` double DEFAULT NULL,
  `meta` JSON DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `core_receiptline_receipt_id_idx` (`receipt_id`),
  CONSTRAINT `core_receiptline_receipt_id_fk` FOREIGN KEY (`receipt_id`)
    REFERENCES `core_receipt` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

DROP_TABLE = "DROP TABLE IF EXISTS `core_receiptline`;"

class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunSQL(CREATE_TABLE, reverse_sql=DROP_TABLE)]
