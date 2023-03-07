"""
Trains a wine quality dataset with a sklearn DecisionTreeRegressor using MLflow manual logging.
The data set used in this example is from http://archive.ics.uci.edu/ml/datasets/Wine+Quality
P. Cortez, A. Cerdeira, F. Almeida, T. Matos and J. Reis.
Modeling wine preferences by data mining from physicochemical properties. In Decision Support Systems, Elsevier, 47(4):547-553, 2009.
"""

import platform
import time
import pandas as pd
import numpy as np
import click
import shortuuid

import sklearn
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeRegressor

import mlflow
import mlflow.sklearn
from mlflow.models.signature import infer_signature
from wine_quality import plot_utils, mlflow_utils
from wine_quality.timestamp_utils import fmt_ts_seconds, fmt_ts_millis
from wine_quality import common

import logging

logger = logging.getLogger(__name__)

logger.debug("Versions:")
logger.debug("  MLflow Version:", mlflow.__version__)
logger.debug("  Sklearn Version:", sklearn.__version__)
logger.debug("  MLflow Tracking URI:", mlflow.get_tracking_uri())
logger.debug("  Python Version:", platform.python_version())
logger.debug("  Operating System:", platform.system()+" - "+platform.release())
logger.debug("  Platform:", platform.platform())

client = mlflow.client.MlflowClient()

col_label = "quality"

now = fmt_ts_seconds(round(time.time()))

class Trainer():
    def __init__(self, experiment_name, data_path, log_as_onnx, save_signature, run_origin=None, use_run_id_as_run_name=False):
        self.experiment_name = experiment_name
        self.data_path = data_path
        self.run_origin = run_origin
        self.log_as_onnx = log_as_onnx
        self.save_signature = save_signature
        self.use_run_id_as_run_name = use_run_id_as_run_name
        self.X_train, self.X_test, self.y_train, self.y_test = self._build_data(data_path)

        if self.experiment_name:
            mlflow.set_experiment(experiment_name)
            exp = client.get_experiment_by_name(experiment_name)
            client.set_experiment_tag(exp.experiment_id, "version_mlflow", mlflow.__version__)
            client.set_experiment_tag(exp.experiment_id, "experiment_created", now)


    def _build_data(self, data_path):
        data = pd.read_csv(data_path)
        train, test = train_test_split(data, test_size=0.30, random_state=42)
    
        # The predicted column is "quality" which is a scalar from [3, 9]
        X_train = train.drop([col_label], axis=1)
        X_test = test.drop([col_label], axis=1)
        y_train = train[[col_label]]
        y_test = test[[col_label]]
        logger.debug(">> X_test.type:",type(X_test))
        logger.debug(">> X_test.dtypes:",X_test.dtypes)

        return X_train, X_test, y_train, y_test 


    def train(self, registered_model_name, registered_model_version_stage="None", archive_existing_versions=False, output_path=None, max_depth=None, max_leaf_nodes=32):
        run_name = f"{now} {self.run_origin} {mlflow.__version__}" if self.run_origin else None
        with mlflow.start_run(run_name=run_name) as run: # NOTE: when running with `mlflow run`, mlflow --run-name option takes precedence!
            run_id = run.info.run_id
            experiment_id = run.info.experiment_id
            logger.debug("MLflow run:")
            logger.debug("  run_id:", run_id)
            logger.debug("  experiment_id:", experiment_id)
            logger.debug("  experiment_name:", client.get_experiment(experiment_id).name)

            # MLflow tags
            if self.use_run_id_as_run_name:
                mlflow.set_tag("mlflow.runName", run_id)
            mlflow.set_tag("run_id", run_id)
            mlflow.set_tag("save_signature", self.save_signature)
            mlflow.set_tag("data_path", self.data_path)
            mlflow.set_tag("registered_model_name", registered_model_name)
            mlflow.set_tag("registered_model_version_stage", registered_model_version_stage)
            mlflow.set_tag("uuid",shortuuid.uuid())
            mlflow.set_tag("dataset", "wine-quality")
            mlflow.set_tag("run_origin", self.run_origin)
            mlflow.set_tag("timestamp", now)
            mlflow.set_tag("version.mlflow", mlflow.__version__)
            mlflow.set_tag("version.sklearn", sklearn.__version__)
            mlflow.set_tag("version.platform", platform.platform())
            mlflow.set_tag("version.python", platform.python_version())

            # Create model
            model = DecisionTreeRegressor(max_depth=max_depth, max_leaf_nodes=max_leaf_nodes)
            logger.debug("Model:\n ", model)

            # Fit and predict
            model.fit(self.X_train, self.y_train)
            predictions = model.predict(self.X_test)

            # MLflow params
            logger.debug("Parameters:")
            logger.debug("  max_depth:", max_depth)
            logger.debug("  max_leaf_nodes:", max_leaf_nodes)
            
            mlflow.log_param("max_depth", max_depth) # NOTE: when running with `mlflow run`, mlflow autologs all -P parameters!!
            mlflow.log_param("max_leaf_nodes", max_leaf_nodes) # ibid

            # MLflow metrics
            rmse = np.sqrt(mean_squared_error(self.y_test, predictions))
            mae = mean_absolute_error(self.y_test, predictions)
            r2 = r2_score(self.y_test, predictions)

            logger.debug("Metrics:")
            logger.debug("  rmse:", rmse)
            logger.debug("  mae:", mae)
            logger.debug("  r2:", r2)
            
            mlflow.log_metric("rmse", rmse)
            mlflow.log_metric("r2", r2)
            mlflow.log_metric("mae", mae)
        
            # Create signature
            signature = infer_signature(self.X_train, predictions) if self.save_signature else None
            logger.debug("Signature:",signature)

            # MLflow log model
            mlflow.sklearn.log_model(model, "model", signature=signature)
            if registered_model_name:
                mlflow_utils.register_model(client, "model", registered_model_name, 
                   registered_model_version_stage, archive_existing_versions, run)

            # Convert sklearn model to ONNX and log model
            if self.log_as_onnx:
                from wine_quality import onnx_utils
                onnx_utils.log_model(model, "onnx-model", registered_model_name, self.X_test)

            # MLflow artifact - plot file
            plot_file = "plot.png"
            plot_utils.create_plot_file(self.y_test, predictions, plot_file)
            mlflow.log_artifact(plot_file)

            # Write run ID to file
            if (output_path):
                mlflow.set_tag("output_path", output_path)
                output_path = output_path.replace("dbfs:","/dbfs")
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(run_id)
            #mlflow.shap.log_explanation(model.predict, self.X_train, "shap") # TODO: errors out

        run = client.get_run(run_id)
        client.set_tag(run_id, "run.info.start_time", run.info.start_time)
        client.set_tag(run_id, "run.info.end_time", run.info.end_time)
        client.set_tag(run_id, "run.info._start_time", fmt_ts_millis(run.info.start_time))
        client.set_tag(run_id, "run.info._end_time", fmt_ts_millis(run.info.end_time))

        return (experiment_id, run_id)

def main(experiment_name, data_path, model_name, model_version_stage, archive_existing_versions, 
        save_signature, log_as_onnx, max_depth, max_leaf_nodes, run_origin, output_path, use_run_id_as_run_name):
    logger.debug("Options:")
    for k,v in locals().items(): 
        logger.debug(f"  {k}: {v}")
    logger.debug("Processed Options:")
    logger.debug(f"  model_name: {model_name} - type: {type(model_name)}")
    trainer = Trainer(experiment_name, data_path, log_as_onnx, save_signature, run_origin, use_run_id_as_run_name)
    trainer.train(model_name, model_version_stage, archive_existing_versions, output_path, max_depth, max_leaf_nodes)


if __name__ == "__main__":
    main()