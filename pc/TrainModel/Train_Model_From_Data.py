import pandas as pd
from sklearn.ensemble import RandomForestRegressor
import pickle
import os

def load_data(csv_file):
    """
    Loads the data from the CSV file.
    """
    data = pd.read_csv(csv_file)
    return data

def prepare_data(data):
    """
    Separates the features and labels.
    """
    # Features: Sensors 0-11 and Sensor_Timestamp
    feature_cols = [f"Sensor_{i}" for i in range(12)] + ['Sensor_Timestamp']
    X = data[feature_cols]

    # Labels: Labels 0-3
    label_cols = [f"Label_{i}" for i in range(4)]
    y = data[label_cols]

    return X, y

def train_model(X, y):
    """
    Trains the Random Forest Regressor model.
    """
    # Initialize the model
    model = RandomForestRegressor(n_estimators=100, random_state=42)

    # Fit the model
    model.fit(X, y)

    return model

def save_model(model, filename):
    """
    Saves the trained model to a file using pickle.
    """
    with open(filename, 'wb') as file:
        pickle.dump(model, file)
    print(f"Model saved to {filename}")

def load_model(filename):
    """
    Loads the model from a file.
    """
    with open(filename, 'rb') as file:
        model = pickle.load(file)
    print(f"Model loaded from {filename}")
    return model

def main():
    # Path to the CSV file
    csv_file = 'filtered_output_70_and_71.csv'

    # Check if the CSV file exists
    if not os.path.exists(csv_file):
        print(f"CSV file not found at {csv_file}")
        return

    # Load the data
    data = load_data(csv_file)
    print("Data loaded successfully.")

    # Prepare the data
    X, y = prepare_data(data)
    print("Data prepared for training.")

    # Train the model
    model = train_model(X, y)
    print("Model trained successfully.")

    # Save the model
    model_filename = 'random_forest_regressor.pkl'
    save_model(model, model_filename)

    # Load the model (for demonstration purposes)
    loaded_model = load_model(model_filename)

    # Example prediction using the loaded model
    sample_input = X.iloc[0:1]  # Take the first sample from the dataset
    prediction = loaded_model.predict(sample_input)
    print(f"Prediction for the first sample: {prediction}")

    from sklearn.model_selection import train_test_split
    from sklearn.metrics import mean_squared_error, r2_score

    # Split the data
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    # Train the model on the training set
    model = train_model(X_train, y_train)

    # Predict on the test set
    y_pred = model.predict(X_test)

    # Evaluate the model
    mse = mean_squared_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)
    print(f"Mean Squared Error: {mse}")
    print(f"R² Score: {r2}")


if __name__ == '__main__':
    main()
