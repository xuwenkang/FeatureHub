import traceback
import sys
import numpy as np
import sklearn.metrics
from collections import defaultdict
from sklearn.preprocessing import label_binarize
from sklearn.model_selection import KFold, StratifiedKFold
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor
from featurefactory.modeling.metrics import Metric, MetricList
from featurefactory.util import RANDOM_STATE

class Model(object):
    """Versatile modeling object.

    Handles classification and regression problems and computes variety of
    performance metrics.

    Parameters
    ----------
    problem_type : str
        One of "classification" or "regression"
    """

    CLASSIFICATION = "classification"
    REGRESSION     = "regression"

    CLASSIFICATION_SCORING = [
        { "name" : "Accuracy"  , "scoring" : "accuracy" },
        { "name" : "Precision" , "scoring" : "precision" },
        { "name" : "Recall"    , "scoring" : "recall" },
        { "name" : "ROC AUC"   , "scoring" : "roc_auc" },
    ]
    REGRESSION_SCORING = [
        { "name" : "Mean Squared Error" , "scoring" : "mean_squared_error" },
        { "name" : "R-squared"          , "scoring" : "r2" },
    ]

    BINARY_METRIC_AGGREGATION = "average"
    MULTICLASS_METRIC_AGGREGATION = "micro"

    def __init__(self, problem_type):
        self.problem_type = problem_type

        if self.problem_type == Model.CLASSIFICATION:
            self.model = DecisionTreeClassifier(random_state=RANDOM_STATE+1)
        elif self.problem_type == Model.REGRESSION:
            self.model = DecisionTreeRegressor(random_state=RANDOM_STATE+2)
        else:
            raise NotImplementedError

    def compute_metrics(self, X, Y, kind="cv", **kwargs):
        if kind=="cv":
            return self.compute_metrics_cv(X, Y, **kwargs)
        elif kind=="train_test":
            return self.compute_metrics_train_test(X, Y, **kwargs)
        else:
            raise ValueError("Bad kind: {}".format(kind))

    def compute_metrics_cv(self, X, Y):
        """Compute cross-validated metrics.

        Trains this model on data X with labels Y.

        Returns a MetricList with the name, scoring type, and value for each
        Metric. Note that these values may be numpy floating points, and should
        be converted prior to insertion in a database.

        Parameters
        ----------
        X : numpy array-like or pd.DataFrame
            data
        Y : numpy array-like or pd.DataFrame or pd.DataSeries
            labels
        """

        # ensure that we use np for everything
        X = np.array(X)
        Y = np.array(Y)

        scorings, scorings_ = self._get_scorings()

        # compute scores
        scores = self.cv_score_mean(X, Y, scorings_)

        # unpack into MetricList
        metric_list = self.scores_to_metriclist(scorings, scores)
        return metric_list

    def compute_metrics_train_test(self, X, Y, n):
        """
        """

        # ensure that we use np for everything, and don't use 1d arrays
        X = np.array(X)
        if X.ndim == 1:
            X = X.reshape(-1,1)
        Y = np.array(Y).ravel()

        X_train, Y_train = X[:n], Y[:n]
        X_test, Y_test = X[n:], Y[n:]

        scorings, scorings_ = self._get_scorings()

        # Determine binary/multiclass classification
        n_classes = len(np.unique(Y))
        if n_classes > 2:
            metric_aggregation = Model.MULTICLASS_METRIC_AGGREGATION
        else:
            metric_aggregation = Model.BINARY_METRIC_AGGREGATION

        params = self._get_params(metric_aggregation, n_classes)

        # fit model on entire training set
        self.model.fit(X_train, Y_train)

        scores = {}
        for scoring in scorings_:
            # Make and evaluate predictions. Note that ROC AUC may raise
            # exception if somehow we only have examples from one class in
            # a given fold.
            Y_test_pred = params[scoring]["predictor"](self.model, X_test)
            try:
                score = params[scoring]["scorer"](Y_test, Y_test_pred)
            except ValueError as e:
                score = None
                print(traceback.format_exc(), file=sys.stderr)
            scores[scoring] = score

        metric_list = self.scores_to_metriclist(scorings, scores)
        return metric_list

    def cv_score_mean(self, X, Y, scorings):
        """Compute mean score across cross validation folds.

        Split data and labels into cross validation folds and fit the model for
        each fold. Then, for each scoring type in scorings, compute the score.
        Finally, average the scores across folds. Returns a dictionary mapping
        scoring to score.

        Parameters
        ----------
        X : numpy array-like
            data
        Y : numpy array-like
            labels
        scorings : list of str
            scoring types
        """
        # 1d arrays are deprecated by sklearn 0.17 (?)
        if X.ndim == 1:
            X = X.reshape(-1, 1)

        Y = Y.ravel()

        scorings = list(scorings)

        # Determine binary/multiclass classification
        n_classes = len(np.unique(Y))
        if n_classes > 2:
            metric_aggregation = Model.MULTICLASS_METRIC_AGGREGATION
        else:
            metric_aggregation = Model.BINARY_METRIC_AGGREGATION

        params = self._get_params(metric_aggregation, n_classes)

        if self._is_classification():
            kf = StratifiedKFold(shuffle=True, random_state=RANDOM_STATE+3)
        else:
            kf = KFold(shuffle=True, random_state=RANDOM_STATE+4)

        # Split data, train model, and evaluate metric. We fit the model just
        # once per fold.
        scoring_outputs = defaultdict(lambda : [])
        for train_inds, test_inds in kf.split(X, Y):
            X_train, X_test = X[train_inds], X[test_inds]
            Y_train, Y_test = Y[train_inds], Y[test_inds]

            self.model.fit(X_train, Y_train)

            for scoring in scorings:
                # Make and evaluate predictions. Note that ROC AUC may raise
                # exception if somehow we only have examples from one class in
                # a given fold.
                Y_test_pred = params[scoring]["predictor"](self.model, X_test)
                try:
                    score = params[scoring]["scorer"](Y_test, Y_test_pred)
                except ValueError as e:
                    score = np.nan
                    print(traceback.format_exc(), file=sys.stderr)
                scoring_outputs[scoring].append(score)

        for scoring in scoring_outputs:
            score_mean = np.nanmean(scoring_outputs[scoring])
            if np.isnan(score_mean):
                score_mean = None
            scoring_outputs[scoring] = score_mean

        return scoring_outputs

    def scores_to_metriclist(self, scorings, scores):
        metric_list = MetricList()
        for v in scorings:
            name    = v["name"]
            scoring = v["scoring"]

            if scoring in scores:
                value = scores[scoring]
            else:
                value = None

            metric_list.append(Metric(name, scoring, value))

        return metric_list

    def _is_classification(self):
        return self.problem_type == "classification"

    def _is_regression(self):
        return self.problem_type == "regression"

    def _get_params(self, metric_aggregation, n_classes):
        # Determine predictor (labels, label probabilities, or values) and
        # scoring function.
        def predict_fn(model, X_test):
            return model.predict(X_test)
        def predict_prob_fn(model, X_test):
            return model.predict_proba(X_test)
        def roc_auc_scorer(y_true, y_pred):
            y_true = label_binarize(y_true, classes=[x for x in
                range(n_classes)])
            return sklearn.metrics.roc_auc_score(y_true, y_pred,
                    average=metric_aggregation)

        params = {
            "accuracy" : {
                "predictor" : predict_fn,
                "scorer" : sklearn.metrics.accuracy_score,
            },
            "precision" : {
                "predictor" : predict_fn,
                "scorer" : lambda y_true, y_pred: sklearn.metrics.precision_score(
                        y_true, y_pred, average=metric_aggregation),
            },
            "recall" : {
                "predictor" : predict_fn,
                "scorer" : lambda y_true, y_pred: sklearn.metrics.recall_score(
                        y_true, y_pred, average=metric_aggregation),
            },
            "roc_auc" : {
                "predictor" : predict_prob_fn,
                "scorer" : roc_auc_scorer,
            },
            "mean_squared_error" : {
                "predictor" : predict_fn,
                "scorer" : sklearn.metrics.mean_squared_error,
            },
            "r2" : {
                "predictor" : predict_fn,
                "scorer" : sklearn.metrics.r2_score
            },
        }

        return params

    def _get_scorings(self):
        """Get scorings for this problem type.

        Returns
        -------
        scorings : list of dict
            Information on metric name and associated "scoring" as defined in
            sklearn.metrics
        scorings_ : list
            List of "scoring" as defined in sklearn.metrics. This is a "utility
            variable" that can be used where we just need the names of the
            scoring functions and not the more complete information.
        """
        # scoring_types maps user-readable name to `scoring`, as argument to
        # cross_val_score
        # See also http://scikit-learn.org/stable/modules/model_evaluation.html#scoring-parameter
        if self._is_classification():
            scorings = Model.CLASSIFICATION_SCORING
            scorings_= [s["scoring"] for s in scorings]
        elif self._is_regression():
            scorings = Model.REGRESSION_SCORING
            scorings_= [s["scoring"] for s in scorings]
        else:
            raise NotImplementedError

        return scorings, scorings_