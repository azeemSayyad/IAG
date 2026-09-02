"""
ML Model Evaluation (Step 21.4)

Evaluates model performance using standard metrics.

Metrics:
- Precision — True positives / (True positives + False positives)
- Recall — True positives / (True positives + False negatives)
- F1 Score — 2 * (Precision * Recall) / (Precision + Recall)
- ROC-AUC — Area under ROC curve
"""

from typing import Dict, List, Tuple
import numpy as np


def calculate_confusion_matrix(
    y_true: List[float],
    y_pred: List[float],
    threshold: float = 0.5,
) -> Dict[str, int]:
    """
    Calculate confusion matrix.

    Returns:
        tp: True positives
        fp: False positives
        tn: True negatives
        fn: False negatives
    """
    tp = 0
    fp = 0
    tn = 0
    fn = 0

    for true, pred in zip(y_true, y_pred):
        pred_binary = 1.0 if pred >= threshold else 0.0

        if true == 1.0 and pred_binary == 1.0:
            tp += 1
        elif true == 0.0 and pred_binary == 1.0:
            fp += 1
        elif true == 0.0 and pred_binary == 0.0:
            tn += 1
        elif true == 1.0 and pred_binary == 0.0:
            fn += 1

    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn}


def calculate_precision(y_true: List[float], y_pred: List[float], threshold: float = 0.5) -> float:
    """
    Calculate precision.

    Precision = TP / (TP + FP)
    """
    cm = calculate_confusion_matrix(y_true, y_pred, threshold)
    tp = cm["tp"]
    fp = cm["fp"]

    if tp + fp == 0:
        return 0.0

    return tp / (tp + fp)


def calculate_recall(y_true: List[float], y_pred: List[float], threshold: float = 0.5) -> float:
    """
    Calculate recall.

    Recall = TP / (TP + FN)
    """
    cm = calculate_confusion_matrix(y_true, y_pred, threshold)
    tp = cm["tp"]
    fn = cm["fn"]

    if tp + fn == 0:
        return 0.0

    return tp / (tp + fn)


def calculate_f1_score(y_true: List[float], y_pred: List[float], threshold: float = 0.5) -> float:
    """
    Calculate F1 score.

    F1 = 2 * (Precision * Recall) / (Precision + Recall)
    """
    precision = calculate_precision(y_true, y_pred, threshold)
    recall = calculate_recall(y_true, y_pred, threshold)

    if precision + recall == 0:
        return 0.0

    return 2 * (precision * recall) / (precision + recall)


def calculate_roc_auc(y_true: List[float], y_pred: List[float]) -> float:
    """
    Calculate ROC-AUC score.

    Uses trapezoidal rule for AUC calculation.
    """
    # Sort by predicted score
    pairs = sorted(zip(y_pred, y_true), reverse=True)

    # Calculate TPR and FPR at each threshold
    n_pos = sum(1 for _, true in pairs if true == 1.0)
    n_neg = sum(1 for _, true in pairs if true == 0.0)

    if n_pos == 0 or n_neg == 0:
        return 0.5

    tps = 0
    fps = 0
    auc = 0.0
    prev_fpr = 0.0
    prev_tpr = 0.0

    for pred, true in pairs:
        if true == 1.0:
            tps += 1
        else:
            fps += 1

        tpr = tps / n_pos
        fpr = fps / n_neg

        # Trapezoidal rule
        auc += (fpr - prev_fpr) * (tpr + prev_tpr) / 2

        prev_fpr = fpr
        prev_tpr = tpr

    return auc


def calculate_accuracy(y_true: List[float], y_pred: List[float], threshold: float = 0.5) -> float:
    """
    Calculate accuracy.
    """
    cm = calculate_confusion_matrix(y_true, y_pred, threshold)
    correct = cm["tp"] + cm["tn"]
    total = cm["tp"] + cm["fp"] + cm["tn"] + cm["fn"]

    if total == 0:
        return 0.0

    return correct / total


def evaluate_model(
    y_true: List[float],
    y_pred: List[float],
    threshold: float = 0.5,
) -> Dict:
    """
    Evaluate model with all metrics.

    Returns:
        precision, recall, f1, roc_auc, accuracy, confusion_matrix
    """
    return {
        "precision": calculate_precision(y_true, y_pred, threshold),
        "recall": calculate_recall(y_true, y_pred, threshold),
        "f1_score": calculate_f1_score(y_true, y_pred, threshold),
        "roc_auc": calculate_roc_auc(y_true, y_pred),
        "accuracy": calculate_accuracy(y_true, y_pred, threshold),
        "confusion_matrix": calculate_confusion_matrix(y_true, y_pred, threshold),
        "threshold": threshold,
        "sample_size": len(y_true),
    }


def evaluate_with_multiple_thresholds(
    y_true: List[float],
    y_pred: List[float],
    thresholds: List[float] = None,
) -> List[Dict]:
    """
    Evaluate model with multiple thresholds.

    Useful for finding optimal threshold.
    """
    if thresholds is None:
        thresholds = [0.3, 0.4, 0.5, 0.6, 0.7]

    results = []
    for threshold in thresholds:
        evaluation = evaluate_model(y_true, y_pred, threshold)
        results.append(evaluation)

    return results


def find_optimal_threshold(
    y_true: List[float],
    y_pred: List[float],
    metric: str = "f1_score",
) -> Tuple[float, float]:
    """
    Find optimal threshold for a metric.

    Returns:
        (optimal_threshold, metric_value)
    """
    thresholds = [i / 100 for i in range(10, 90, 5)]
    best_threshold = 0.5
    best_value = 0.0

    for threshold in thresholds:
        evaluation = evaluate_model(y_true, y_pred, threshold)
        value = evaluation.get(metric, 0)

        if value > best_value:
            best_value = value
            best_threshold = threshold

    return best_threshold, best_value


def cross_validate(
    X: List[Dict],
    y: List[float],
    predict_fn,
    n_folds: int = 5,
) -> Dict:
    """
    Perform k-fold cross-validation.

    Returns:
        mean_metrics: Average metrics across folds
        fold_metrics: Metrics for each fold
    """
    # Split data into folds
    fold_size = len(X) // n_folds
    fold_metrics = []

    for i in range(n_folds):
        # Split into train and test
        test_start = i * fold_size
        test_end = test_start + fold_size

        X_test = X[test_start:test_end]
        y_test = y[test_start:test_end]

        X_train = X[:test_start] + X[test_end:]
        y_train = y[:test_start] + y[test_end:]

        # Train and predict
        # Note: predict_fn should handle training internally
        y_pred = [predict_fn(x, X_train, y_train) for x in X_test]

        # Evaluate
        metrics = evaluate_model(y_test, y_pred)
        fold_metrics.append(metrics)

    # Calculate mean metrics
    mean_metrics = {
        "precision": np.mean([m["precision"] for m in fold_metrics]),
        "recall": np.mean([m["recall"] for m in fold_metrics]),
        "f1_score": np.mean([m["f1_score"] for m in fold_metrics]),
        "roc_auc": np.mean([m["roc_auc"] for m in fold_metrics]),
        "accuracy": np.mean([m["accuracy"] for m in fold_metrics]),
    }

    return {
        "mean_metrics": mean_metrics,
        "fold_metrics": fold_metrics,
        "n_folds": n_folds,
    }
