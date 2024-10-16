from airflow import DAG
from airflow.models import Variable
from airflow.decorators import task
from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook

from datetime import timedelta, datetime
import snowflake.connector
import requests


def return_snowflake_conn():
    # Initialize the SnowflakeHook
    hook = SnowflakeHook(snowflake_conn_id='snowflake_conn')
    # Execute the query and fetch results
    conn = hook.get_conn()
    return conn.cursor()


@task
def train(cur, train_input_table, train_view, forecast_function_name):
    """
     - Create a view with training-related columns
     - Create a model with the view above
    """

    create_view_sql = f"""CREATE OR REPLACE VIEW {train_view} AS 
        SELECT 
            DATE::TIMESTAMP_NTZ AS DATE,  -- Cast DATE to TIMESTAMP_NTZ
            CLOSE, 
            SYMBOL 
        FROM {train_input_table};"""

    # Renaming UDF to avoid conflict with existing procedure
    new_forecast_function_name = f"{forecast_function_name}_UDF"

    create_model_sql = f"""CREATE OR REPLACE FUNCTION {new_forecast_function_name}() RETURNS VARIANT LANGUAGE JAVASCRIPT AS
    $$
        var result = snowflake.execute(`SELECT * FROM {train_view}`);
        return result;
    $$;"""

    try:
        cur.execute(create_view_sql)
        cur.execute(create_model_sql)
        # Inspect the accuracy metrics of your model.
        cur.execute(f"CALL {new_forecast_function_name}();")
    except Exception as e:
        print(e)
        raise


@task
def predict(cur, forecast_function_name, train_input_table, forecast_table, final_table):
    """
     - Generate predictions and store the results to a table named forecast_table.
     - Union your predictions with your historical data, then create the final table
    """
    # Update the function name in the predict block as well
    new_forecast_function_name = f"{forecast_function_name}_UDF"

    make_prediction_sql = f"""BEGIN
        -- This is the step that creates your predictions.
        LET x := (SELECT * FROM TABLE({new_forecast_function_name}()));
        -- These steps store your predictions to a table.
        CREATE OR REPLACE TABLE {forecast_table} AS SELECT * FROM TABLE(RESULT_SCAN(:x));
    END;"""

    create_final_table_sql = f"""CREATE OR REPLACE TABLE {final_table} AS
        SELECT SYMBOL, DATE, CLOSE AS actual, NULL AS forecast, NULL AS lower_bound, NULL AS upper_bound
        FROM {train_input_table}
        UNION ALL
        SELECT replace(series, '"', '') as SYMBOL, ts as DATE, NULL AS actual, forecast, lower_bound, upper_bound
        FROM {forecast_table};"""

    try:
        cur.execute(make_prediction_sql)
        cur.execute(create_final_table_sql)
    except Exception as e:
        print(e)
        raise


default_args = {
   'owner': 'ariel',
   'depends_on_past': False,
   'email': ['ariel.hsieh@sjsu.edu'],
   'retries': 1,
   'retry_delay': timedelta(minutes=3),
}


with DAG(
    dag_id = 'TrainPredict',
    start_date = datetime(2024,10,11),
    catchup=False,
    tags=['ML', 'ELT'],
    schedule = '32 23 * * *',
    default_args=default_args
) as dag:

    train_input_table = "stock_price_db.raw_data.stock_price"
    train_view = "stock_price_db.adhoc.stock_price_view"
    forecast_table = "stock_price_db.adhoc.stock_price_forecast"
    forecast_function_name = "stock_price_db.analytics.predict_stock_price"
    final_table = "stock_price_db.analytics.stock_price"
    cur = return_snowflake_conn()

    train(cur, train_input_table, train_view, forecast_function_name)
    predict(cur, forecast_function_name, train_input_table, forecast_table, final_table)
