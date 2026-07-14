#!/usr/bin/env python3
"""
Feature Engineering Pipeline - Semana 5
Log Monitor MLOps Project

Extrai features de logs para machine learning:
- 15+ features base (numéricas, agregação, temporais, URL)
- Features avançadas (entropy, behavioral)
- Feature selection (correlation + RFE)
- Normalization (StandardScaler)
"""

import pandas as pd
import numpy as np
import psycopg2
from scipy.stats import entropy as shannon_entropy
from sklearn.feature_selection import RFE
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from dotenv import load_dotenv
import os
import pickle
import json
from collections import Counter
from datetime import datetime
import warnings

warnings.filterwarnings('ignore')
load_dotenv()

DEFAULT_LOOKBACK_DAYS = int(os.getenv('FEATURE_LOOKBACK_DAYS', '90'))


class FeatureEngineer:
    """Pipeline completo de feature engineering para anomaly detection"""
    
    def __init__(self, connect_db=True):
        self.conn = None
        self.cursor = None
        self._setup_directories()
        if connect_db:
            self._connect_db()
    
    def _setup_directories(self):
        """Cria diretórios necessários"""
        for directory in ['data', 'models']:
            os.makedirs(directory, exist_ok=True)
        print("✓ Diretórios criados/verificados")
    
    def _connect_db(self):
        """Conecta ao PostgreSQL"""
        try:
            self.conn = psycopg2.connect(
                host=os.getenv("POSTGRES_HOST", "localhost"),
                port=int(os.getenv("POSTGRES_PORT", "5432")),
                database=os.getenv("POSTGRES_DB", "logmonitor"),
                user=os.getenv("POSTGRES_USER", "postgres"),
                password=os.getenv("POSTGRES_PASSWORD", "changeme")
            )
            self.cursor = self.conn.cursor()
            print("✓ Conectado ao PostgreSQL")
        except Exception as e:
            print(f"✗ Erro ao conectar PostgreSQL: {e}")
            raise
    
    def load_data(self, days=DEFAULT_LOOKBACK_DAYS):
        """
        Carrega logs dos últimos N dias
        
        Args:
            days: Número de dias para carregar (default: 7)
        
        Returns:
            DataFrame com logs
        """
        print(f"\n1. Carregando logs dos últimos {days} dias...")
        
        query = f"""
        SELECT 
            id,
            timestamp,
            ip,
            method,
            endpoint,
            status,
            response_time_ms,
            user_agent,
            data
        FROM raw_logs
        WHERE timestamp > NOW() - INTERVAL '{days} days'
        ORDER BY timestamp
        """
        
        try:
            df = pd.read_sql(query, self.conn)
            
            if len(df) == 0:
                raise ValueError(f"Nenhum log encontrado nos últimos {days} dias!")
            
            print(f"   ✓ {len(df):,} logs carregados")
            print(f"   ✓ Período: {df['timestamp'].min()} até {df['timestamp'].max()}")
            
            return df
            
        except Exception as e:
            print(f"   ✗ Erro ao carregar dados: {e}")
            raise
    
    def _prepare_raw_logs_frame(self, df):
        """Normaliza logs crus para suportar fontes reais com campos em falta."""
        df = df.copy()
        fallback_timestamp = pd.Timestamp.now(tz='UTC')

        def ensure_series(column, default_value):
            if column in df.columns:
                return df[column]
            return pd.Series([default_value] * len(df), index=df.index)

        df['timestamp'] = pd.to_datetime(
            ensure_series('timestamp', fallback_timestamp),
            utc=True,
            errors='coerce',
        ).fillna(fallback_timestamp)
        df['ip'] = ensure_series('ip', '0.0.0.0').fillna('0.0.0.0').astype(str)
        df['method'] = ensure_series('method', 'UNKNOWN').fillna('UNKNOWN').astype(str)
        df['endpoint'] = ensure_series('endpoint', '/unknown').fillna('/unknown').astype(str)
        df['user_agent'] = ensure_series('user_agent', 'unknown').fillna('unknown').astype(str)

        status = pd.to_numeric(ensure_series('status', 0), errors='coerce').fillna(0)
        df['status'] = status.astype(int)

        response_time = pd.to_numeric(
            ensure_series('response_time_ms', np.nan),
            errors='coerce',
        )
        response_time_median = response_time.dropna().median()
        if pd.isna(response_time_median):
            response_time_median = 0.0
        df['response_time_ms'] = response_time.fillna(float(response_time_median)).astype(float)

        df['data'] = ensure_series('data', {}).apply(
            lambda payload: payload if isinstance(payload, dict) else {}
        )
        return df

    def extract_features(self, df):
        """
        Pipeline completo de extração de features
        
        Args:
            df: DataFrame com logs brutos
        
        Returns:
            DataFrame com features extraídas
        """
        print("\n" + "="*70)
        print("FEATURE ENGINEERING PIPELINE")
        print("="*70 + "\n")
        
        # Cria c?pia normalizada para suportar fontes reais
        df = self._prepare_raw_logs_frame(df)
        
        # Pipeline de extração
        features = self._numeric_features(df)
        features = self._aggregation_features(df, features)
        features = self._temporal_features(df, features)
        features = self._url_features(df, features)
        features = self._entropy_features(df, features)
        features = self._behavioral_features(df, features)
        features = self._baseline_features(df, features)
        
        print(f"\n✓ Total de features extraídas: {len(features.columns)}")
        print(f"✓ Shape: {features.shape}")
        
        return features, df
    
    def _numeric_features(self, df):
        """Features numéricas diretas (2 features)"""
        print("2. Extraindo features numéricas...")
        
        features = pd.DataFrame({
            'status_code': df['status'].astype(float),
            'response_time_ms': df['response_time_ms'].astype(float)
        })
        
        print(f"   ✓ 2 features numéricas")
        return features
    
    def _aggregation_features(self, df, features):
        """Features de agregacao por IP - janela 5min (6 features)"""
        print("3. Extraindo features de agregacao (janela 5min)...")

        time_window = df['timestamp'].dt.floor('5min')
        group_keys = [df['ip'], time_window]

        requests_per_ip = df.groupby(group_keys)['id'].transform('count')
        unique_endpoints = df.groupby(group_keys)['endpoint'].transform('nunique')
        failed_ratio = (df['status'] >= 400).astype(float).groupby(group_keys).transform('mean')
        avg_response = df.groupby(group_keys)['response_time_ms'].transform('mean')
        max_response = df.groupby(group_keys)['response_time_ms'].transform('max')

        features['requests_per_ip_5min'] = requests_per_ip.fillna(1).astype(float)
        features['unique_endpoints_5min'] = unique_endpoints.fillna(1).astype(float)
        features['failed_requests_ratio_5min'] = failed_ratio.fillna(0.0).astype(float)
        features['avg_response_time_5min'] = avg_response.fillna(df['response_time_ms']).astype(float)
        features['max_response_time_5min'] = max_response.fillna(df['response_time_ms']).astype(float)
        features['request_rate_5min'] = features['requests_per_ip_5min'] / 300.0

        print("   OK 6 features de agregacao")
        return features

    def _temporal_features(self, df, features):
        """Features temporais (4 features)"""
        print("4. Extraindo features temporais...")

        timestamps = df['timestamp']

        features['hour_of_day'] = timestamps.dt.hour.astype(float)
        features['day_of_week'] = timestamps.dt.dayofweek.astype(float)  # 0=Monday
       # features['is_weekend'] = (timestamps.dt.dayofweek >= 5).astype(float)
        features['is_night'] = ((timestamps.dt.hour >= 22) | (timestamps.dt.hour < 6)).astype(float)

        print("   OK 4 features temporais")
        return features

    def _url_features(self, df, features):
        """Features de URL/endpoint (3 features)"""
        print("5. Extraindo features de URL...")

        endpoint_series = df['endpoint']
        has_query_params = endpoint_series.str.contains(r'\?', regex=True)
        features['endpoint_length'] = endpoint_series.str.len().astype(float)
        features['has_query_params'] = has_query_params.astype(float)
        features['query_param_count'] = (
            endpoint_series.str.count('&').fillna(0) + has_query_params.astype(int)
        ).astype(float)

        print("   OK 3 features de URL")
        return features

    def _entropy_features(self, df, features):
        """Features de entropy (1 feature)"""
        print("6. Extraindo features de entropy...")

        def calculate_entropy(text):
            """Calcula Shannon entropy de uma string"""
            if not text:
                return 0.0

            counts = np.fromiter(Counter(text).values(), dtype=float)
            if counts.size == 0:
                return 0.0

            freq = counts / counts.sum()
            return float(shannon_entropy(freq, base=2))

        endpoint_entropy_map = {
            endpoint: calculate_entropy(endpoint)
            for endpoint in df['endpoint'].dropna().astype(str).unique()
        }
        features['endpoint_entropy'] = df['endpoint'].map(endpoint_entropy_map).fillna(0.0).astype(float)

        print("   OK 1 feature de entropy")
        return features

    def _behavioral_features(self, df, features):
        """Features comportamentais (2 features)"""
        print("7. Extraindo features comportamentais...")

        df_sorted = df.sort_values(['ip', 'timestamp']).copy()
        time_since_last = df_sorted.groupby('ip')['timestamp'].diff().dt.total_seconds()
        median_time = time_since_last.median()
        if pd.isna(median_time):
            median_time = 999999.0
        time_since_last = time_since_last.fillna(float(median_time))

        # O rolling count original equivale a min(cumcount + 1, 60).
        requests_per_minute = df_sorted.groupby('ip').cumcount().add(1).clip(upper=60).astype(float)

        df_sorted['time_since_last_request'] = time_since_last
        df_sorted['requests_per_minute'] = requests_per_minute
        df_sorted = df_sorted.sort_index()

        features['time_since_last_request'] = df_sorted['time_since_last_request'].astype(float).fillna(999999.0)
        features['requests_per_minute'] = df_sorted['requests_per_minute'].astype(float)

        print("   OK 2 features comportamentais")
        return features

    def _baseline_features(self, df, features):
        """Features orientadas a baseline de normalidade para novelty detection"""
        print("8. Extraindo features de baseline comportamental...")

        response_time = df['response_time_ms'].astype(float)
        response_mean = response_time.mean()
        response_std = response_time.std() or 1.0
        features['response_time_log'] = np.log1p(response_time)
        features['response_time_zscore_global'] = ((response_time - response_mean) / response_std).clip(-10, 10)

        endpoint_stats = df.groupby('endpoint')['response_time_ms'].agg(['mean', 'std']).rename(columns={'mean': 'endpoint_rt_mean', 'std': 'endpoint_rt_std'})
        df_enriched = df.join(endpoint_stats, on='endpoint')
        endpoint_std = df_enriched['endpoint_rt_std'].replace(0, np.nan).fillna(response_std)
        features['response_time_zscore_endpoint'] = ((response_time - df_enriched['endpoint_rt_mean'].fillna(response_mean)) / endpoint_std).clip(-10, 10)

        ip_request_baseline = features.groupby(df['ip'])['requests_per_ip_5min'].transform('median').replace(0, 1)
        features['requests_vs_ip_baseline_ratio'] = (features['requests_per_ip_5min'] / ip_request_baseline).clip(0, 50)

        endpoint_frequency = df['endpoint'].value_counts(normalize=True)
        method_endpoint_keys = df['method'].astype(str) + ':' + df['endpoint'].astype(str)
        method_endpoint_frequency = method_endpoint_keys.value_counts(normalize=True)
        features['endpoint_rarity'] = 1.0 - df['endpoint'].map(endpoint_frequency).fillna(0.0)
        features['method_endpoint_rarity'] = 1.0 - method_endpoint_keys.map(method_endpoint_frequency).fillna(0.0)

        features['error_burst_score'] = (features['failed_requests_ratio_5min'] * features['requests_per_ip_5min']).clip(0, 1000)

        timestamp_hours = df['timestamp'].dt.hour.astype(float)
        endpoint_hour_baseline = df.assign(timestamp_hour=timestamp_hours).groupby('endpoint')['timestamp_hour'].transform('median')
        features['hour_deviation_from_endpoint_pattern'] = (timestamp_hours - endpoint_hour_baseline).abs().fillna(0).clip(0, 12)

        print("   OK 7 features de baseline")
        return features

    def get_iforest_feature_columns(self, features):
        """Conjunto dedicado de features para novelty detection."""
        preferred = [
            'status_code',
            'response_time_ms',
            'requests_per_ip_5min',
            'unique_endpoints_5min',
            'failed_requests_ratio_5min',
            'avg_response_time_5min',
            'max_response_time_5min',
            'request_rate_5min',
            'hour_of_day',
            'day_of_week',
            'is_night',
            'endpoint_length',
            'has_query_params',
            'query_param_count',
            'endpoint_entropy',
            'time_since_last_request',
            'requests_per_minute',
            'response_time_log',
            'response_time_zscore_global',
            'response_time_zscore_endpoint',
            'requests_vs_ip_baseline_ratio',
            'endpoint_rarity',
            'method_endpoint_rarity',
            'error_burst_score',
            'hour_deviation_from_endpoint_pattern',
        ]
        return [col for col in preferred if col in features.columns]

    def create_labels(self, df):
        """
        Cria labels baseados em alertas existentes
        
        Args:
            df: DataFrame com logs
        
        Returns:
            Array com labels (0=normal, 1=anomalia)
        """
        print("\n8. Criando labels a partir de alertas...")
        
        try:
            query = """
            SELECT DISTINCT UNNEST(log_ids) as log_id
            FROM alerts
            """
            alert_log_ids = pd.read_sql(query, self.conn)['log_id'].tolist()
            
            # Label: 1 se log está em alerta, 0 caso contrário
            labels = df['id'].isin(alert_log_ids).astype(int)
            
            anomaly_rate = labels.mean() * 100
            
            print(f"   ✓ Labels criados:")
            print(f"     • Anomalias: {labels.sum():,} ({anomaly_rate:.2f}%)")
            print(f"     • Normais: {(~labels.astype(bool)).sum():,} ({100-anomaly_rate:.2f}%)")
            
            if labels.sum() == 0:
                print("\n   ⚠️  WARNING: Nenhuma anomalia encontrada!")
                print("      Execute: python src/detection/rule_engine.py --mode historical --days 30")
                raise ValueError("Sem labels para treinar RFE!")
            
            if anomaly_rate < 1:
                print(f"\n   ⚠️  WARNING: Anomaly rate muito baixo ({anomaly_rate:.2f}%)")
                print("      Recomendado: > 1% para RFE robusto")
            
            return labels
            
        except Exception as e:
            print(f"   ✗ Erro ao criar labels: {e}")
            raise
    
    def remove_correlated_features(self, features, threshold=0.9):
        """
        Remove features com correlação > threshold
        
        Args:
            features: DataFrame com features
            threshold: Limite de correlação (default: 0.9)
        
        Returns:
            DataFrame sem features correlacionadas
        """
        print(f"\n9. Removendo features correlacionadas (threshold={threshold})...")
        
        # Calcular matriz de correlação
        corr_matrix = features.corr().abs()
        
        # Matriz triangular superior
        upper = corr_matrix.where(
            np.triu(np.ones(corr_matrix.shape), k=1).astype(bool)
        )
        
        # Features para remover
        to_drop = [column for column in upper.columns 
                   if any(upper[column] > threshold)]
        
        if len(to_drop) > 0:
            print(f"   ✓ Features altamente correlacionadas: {to_drop}")
            print(f"   ✓ Removendo {len(to_drop)} features")
            features = features.drop(columns=to_drop)
        else:
            print(f"   ✓ Nenhuma feature com correlação > {threshold}")
        
        print(f"   ✓ Features restantes: {len(features.columns)}")
        
        return features
    
    def select_best_features(self, features, labels, n_features=20):
        """
        Seleciona top N features usando RFE
        
        Args:
            features: DataFrame com features
            labels: Array com labels
            n_features: Número de features a selecionar
        
        Returns:
            Tuple (features_selecionadas, nomes_colunas)
        """
        print(f"\n10. Seleção de features com RFE (top {n_features})...")
        
        # Ajusta n_features se necessário
        n_features = min(n_features, len(features.columns))
        
        # Random Forest como estimador
        estimator = RandomForestClassifier(
            n_estimators=100,
            random_state=42,
            n_jobs=-1,
            class_weight='balanced'  # Importante para label imbalance
        )
        
        # RFE
        print("   → Executando RFE (pode demorar 30-60s)...")
        selector = RFE(
            estimator=estimator,
            n_features_to_select=n_features,
            step=1,
            verbose=0
        )
        
        try:
            selector.fit(features, labels)
        except Exception as e:
            print(f"   ✗ Erro no RFE: {e}")
            raise
        
        # Features selecionadas
        selected_features = features.columns[selector.support_].tolist()
        
        print(f"\n   ✓ Features selecionadas ({len(selected_features)}):")
        for i, feat in enumerate(selected_features, 1):
            ranking = selector.ranking_[features.columns.get_loc(feat)]
            print(f"      {i:2d}. {feat:30s} (rank: {ranking})")
        
        # Guardar selector
        with open('models/feature_selector.pkl', 'wb') as f:
            pickle.dump(selector, f)
        print("\n   ✓ Selector salvo: models/feature_selector.pkl")
        
        return features[selected_features], selected_features
    
    def normalize_features(self, features, scaler_path='models/scaler.pkl', step_name='11'):
        """
        Normaliza features com StandardScaler
        
        Args:
            features: DataFrame com features
        
        Returns:
            Tuple (features_normalizadas, scaler)
        """
        print(f"\n{step_name}. Normalizando features...")
        
        scaler = StandardScaler()
        features_scaled = pd.DataFrame(
            scaler.fit_transform(features),
            columns=features.columns,
            index=features.index
        )
        
        # Guardar scaler
        with open(scaler_path, 'wb') as f:
            pickle.dump(scaler, f)
        
        print("   ✓ Features normalizadas (StandardScaler)")
        print("   ✓ Scaler salvo: models/scaler.pkl")
        
        return features_scaled, scaler
    
    def build_protocol_metadata(self, df):
        """Constroi metadados para o protocolo de novelty detection."""
        scenario = df['data'].apply(
            lambda payload: payload.get('scenario') if isinstance(payload, dict) else None
        ).fillna('observed').astype(str)

        known_attack_scenarios = {'brute_force', 'rate_abuse', 'offhours'}
        unknown_attack_scenarios = {'scanning', 'sql_injection'}

        protocol_role = pd.Series('observed', index=df.index, dtype='object')
        protocol_role[scenario == 'normal'] = 'baseline_normal'
        protocol_role[scenario.isin(known_attack_scenarios)] = 'known_attack'
        protocol_role[scenario.isin(unknown_attack_scenarios)] = 'unknown_attack'

        timestamps = pd.to_datetime(df['timestamp'])
        protocol_split = pd.Series('observed', index=df.index, dtype='object')
        normal_mask = scenario == 'normal'
        normal_timestamps = timestamps[normal_mask]
        if not normal_timestamps.empty:
            train_cutoff = normal_timestamps.quantile(0.70)
            validation_cutoff = normal_timestamps.quantile(0.85)
            protocol_split[normal_mask & (timestamps <= train_cutoff)] = 'baseline_train'
            protocol_split[normal_mask & (timestamps > train_cutoff) & (timestamps <= validation_cutoff)] = 'threshold_validation'
            protocol_split[normal_mask & (timestamps > validation_cutoff)] = 'novelty_test'

        protocol_split[scenario.isin(known_attack_scenarios)] = 'threshold_validation'
        protocol_split[scenario.isin(unknown_attack_scenarios)] = 'novelty_test'

        return scenario, protocol_role, protocol_split

    def save_artifacts(self, df_original, features_final, labels, selected_cols, iforest_features_final, iforest_cols, lookback_days):
        """
        Guarda todos os artefactos
        
        Args:
            df_original: DataFrame original com logs
            features_final: Features finais normalizadas
            labels: Labels
            selected_cols: Nomes das colunas selecionadas
            iforest_features_final: Dataset dedicado ao Isolation Forest
            iforest_cols: Nomes das colunas dedicadas ao Isolation Forest
            lookback_days: Janela temporal usada para carregar logs
        """
        print("\n12. Salvando artefactos...")
        
        scenario, protocol_role, protocol_split = self.build_protocol_metadata(df_original)

        # Dataset completo
        dataset = features_final.copy()
        dataset['label'] = labels.values
        dataset['log_id'] = df_original['id'].values
        dataset['timestamp'] = df_original['timestamp'].values
        dataset['scenario'] = scenario.values
        dataset['protocol_role'] = protocol_role.values
        dataset['protocol_split'] = protocol_split.values
        
        # CSV
        dataset.to_csv('data/ml_dataset.csv', index=False)
        print("   ? data/ml_dataset.csv")
        
        # Pickle (mais r?pido para ML)
        dataset.to_pickle('data/ml_dataset.pkl')
        print("   ? data/ml_dataset.pkl")

        iforest_dataset = iforest_features_final.copy()
        iforest_dataset['label'] = labels.values
        iforest_dataset['log_id'] = df_original['id'].values
        iforest_dataset['timestamp'] = df_original['timestamp'].values
        iforest_dataset['scenario'] = scenario.values
        iforest_dataset['protocol_role'] = protocol_role.values
        iforest_dataset['protocol_split'] = protocol_split.values
        iforest_dataset.to_csv('data/ml_dataset_iforest.csv', index=False)
        print("   ? data/ml_dataset_iforest.csv")
        iforest_dataset.to_pickle('data/ml_dataset_iforest.pkl')
        print("   ? data/ml_dataset_iforest.pkl")
        
        # Feature names
        with open('data/selected_features.txt', 'w') as f:
            f.write('\n'.join(selected_cols))
        print("   ? data/selected_features.txt")

        with open('data/iforest_features.txt', 'w') as f:
            f.write('\n'.join(iforest_cols))
        print("   ? data/iforest_features.txt")
        
        # Summary
        summary = {
            'timestamp': datetime.now().isoformat(),
            'total_logs': len(df_original),
            'total_features_extracted': int(features_final.shape[1] + max(len(iforest_cols) - len(selected_cols), 0)),
            'total_features_selected': len(selected_cols),
            'anomalies': int(labels.sum()),
            'anomaly_rate': f"{100*labels.mean():.2f}%",
            'selected_features': selected_cols,
            'iforest_features': iforest_cols,
            'iforest_feature_count': len(iforest_cols),
            'protocol_split_counts': protocol_split.value_counts().to_dict(),
            'protocol_role_counts': protocol_role.value_counts().to_dict(),
            'lookback_days': lookback_days,
            'period_start': str(df_original['timestamp'].min()),
            'period_end': str(df_original['timestamp'].max())
        }
        
        with open('data/feature_summary.json', 'w') as f:
            json.dump(summary, f, indent=2)
        print("   ✓ data/feature_summary.json")
        
        print(f"\n   ✓ Dataset final shape: {dataset.shape}")
    
    def run_pipeline(self, days=DEFAULT_LOOKBACK_DAYS, n_features_final=20):
        """
        Pipeline completo end-to-end
        
        Args:
            days: Dias para carregar (default: FEATURE_LOOKBACK_DAYS)
            n_features_final: N?mero de features finais (default: 20)
        
        Returns:
            Tuple (features_scaled, labels)
        """
        print("=" * 70)
        print("FEATURE ENGINEERING PIPELINE - PLANO A+")
        print("Semana 5 - Log Monitor MLOps")
        print("=" * 70)
        
        try:
            df = self.load_data(days)
            features, _ = self.extract_features(df)
            labels = self.create_labels(df)
            features = self.remove_correlated_features(features)

            iforest_cols = self.get_iforest_feature_columns(features)
            iforest_features = features[iforest_cols].copy()
            print(f"\n10.1 Features dedicadas ao Isolation Forest ({len(iforest_cols)}): {iforest_cols}")

            features_selected, selected_cols = self.select_best_features(
                features, labels, n_features=n_features_final
            )

            features_final, scaler = self.normalize_features(
                features_selected,
                scaler_path='models/scaler.pkl',
                step_name='11',
            )
            iforest_features_final, iforest_scaler = self.normalize_features(
                iforest_features,
                scaler_path='models/iforest_scaler.pkl',
                step_name='11.1',
            )

            self.save_artifacts(
                df,
                features_final,
                labels,
                selected_cols,
                iforest_features_final,
                iforest_cols,
                lookback_days=days,
            )

            print("\n" + "=" * 70)
            print("? PIPELINE COMPLETO COM SUCESSO!")
            print("=" * 70)
            print("\nArtefactos criados:")
            print("  ? data/ml_dataset.pkl (dataset supervisionado)")
            print("  ? data/ml_dataset_iforest.pkl (dataset novelty detection)")
            print("  ? data/ml_dataset.csv (vers?o CSV supervisionada)")
            print("  ? data/ml_dataset_iforest.csv (vers?o CSV IF)")
            print("  ? models/scaler.pkl (StandardScaler supervisionado)")
            print("  ? models/iforest_scaler.pkl (StandardScaler IF)")
            print("  ? models/feature_selector.pkl (RFE supervisionado)")
            print("  ? data/selected_features.txt (lista supervisionada)")
            print("  ? data/iforest_features.txt (lista novelty detection)")
            print("  ? data/feature_summary.json (metadados)")
            print("\nPr?ximo passo:")
            print("  ? Treinar novamente o Isolation Forest com dataset dedicado")
            return features_final, labels

        except Exception as e:
            print(f"\n? ERRO NO PIPELINE: {e}")
            raise

        finally:
            if self.conn:
                self.conn.close()
                print("\n? Conex?o PostgreSQL fechada")


def main():
    """Fun??o principal"""
    try:
        fe = FeatureEngineer()
        features, labels = fe.run_pipeline(days=DEFAULT_LOOKBACK_DAYS, n_features_final=20)

        print(f"\n{'=' * 70}")
        print("RESUMO FINAL")
        print(f"{'=' * 70}")
        print(f"Total de samples: {len(features):,}")
        print(f"Anomalias: {labels.sum():,} ({100 * labels.mean():.2f}%)")
        print(f"Normais: {(labels == 0).sum():,} ({100 * (labels == 0).mean():.2f}%)")
        return features, labels

    except Exception as e:
        print(f"\n? Falha: {e}")
        print("\nTroubleshooting:")
        print("1. PostgreSQL est? a correr? docker compose ps")
        print("2. Tens logs? SELECT COUNT(*) FROM raw_logs;")
        print("3. Tens alertas? SELECT COUNT(*) FROM alerts;")
        print("4. Depend?ncias instaladas? pip install -r requirements.txt")
        raise


if __name__ == '__main__':
    main()


