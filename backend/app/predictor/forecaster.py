from prophet import Prophet
import pandas as pd
from datetime import datetime
from loguru import logger

class Forecaster:
    def __init__(self):
        self.models = {}
        logger.info("Forecaster initialized")

    def prepare_data(self, metrics_list):
        if not metrics_list:
            return None
        data = []
        for m in metrics_list:
            data.append({
                'ds': pd.to_datetime(m.recorded_at),
                'y': float(m.memory_percent)
            })
        df = pd.DataFrame(data)
        return df.sort_values('ds')

    def train(self, pod_name, metrics_list):
        try:
            df = self.prepare_data(metrics_list)
            if df is None or len(df) < 3:
                return None
            model = Prophet(yearly_seasonality=False, weekly_seasonality=False)
            model.fit(df)
            self.models[pod_name] = {
                'model': model,
                'trained_at': datetime.utcnow(),
                'data_points': len(df)
            }
            logger.info(f"Trained Prophet for {pod_name} on {len(df)} points")
            return True
        except Exception as e:
            logger.error(f"Training failed: {e}")
            return None

    def predict(self, pod_name, hours_ahead=1):
        try:
            if pod_name not in self.models:
                return None
            model = self.models[pod_name]['model']
            future = model.make_future_dataframe(periods=hours_ahead*60, freq='min')
            forecast = model.predict(future)
            future_forecast = forecast.tail(hours_ahead*60)
            avg_pred = future_forecast['yhat'].mean()
            max_pred = future_forecast['yhat'].max()
            trend = (future_forecast['yhat'].iloc[-1] - future_forecast['yhat'].iloc[0]) / len(future_forecast)
            confidence = min(100, 80 + (10 * (1 - future_forecast['yhat_upper'].mean() / 100)))
            return {
                'pod_name': pod_name,
                'predicted_at': datetime.utcnow().isoformat(),
                'hours_ahead': hours_ahead,
                'avg_predicted': round(float(avg_pred), 2),
                'max_predicted': round(float(max_pred), 2),
                'trend': 'up' if trend > 0 else 'down',
                'confidence': round(float(confidence), 1)
            }
        except Exception as e:
            logger.error(f"Prediction failed: {e}")
            return None

    def escalation_risk(self, pod_name, metrics_list, critical_threshold=80):
        try:
            prediction = self.predict(pod_name, hours_ahead=2)
            if not prediction:
                return None
            will_exceed = prediction['max_predicted'] > critical_threshold
            escalation_prob = prediction['confidence'] / 100 if will_exceed else 0
            return {
                'pod_name': pod_name,
                'escalation_probability': round(escalation_prob, 2),
                'will_go_critical': will_exceed,
                'current_prediction': prediction['avg_predicted'],
                'confidence': prediction['confidence']
            }
        except Exception as e:
            logger.error(f"Escalation risk failed: {e}")
            return None
